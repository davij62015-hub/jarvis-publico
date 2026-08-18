"""
CHAT CPA - Site Publico
Chat com IA + geracao de imagem + conversor + modo de voz + rede social (Social CPA) + suporte.
NAO tem nenhum comando de controle de PC.
"""

import os
import io
import json
import sqlite3
import urllib.parse
import urllib.request
import random
import uuid
import base64
import secrets
import smtplib
import ssl
import threading
import queue
import time
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, session, redirect, url_for, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import requests
except ImportError:
    requests = None

try:
    import base64 as _b64
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    Fernet = None
    InvalidToken = Exception

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "troque_essa_chave_em_producao")


@app.after_request
def _adicionar_cabecalhos_seguranca(resposta):
    """Protege as pastas do site: impede listagem de diretorio, sniffing de tipo de
    arquivo e que o site seja carregado dentro de um iframe de outro dominio."""
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "SAMEORIGIN"
    resposta.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resposta


@app.route("/static/uploads/")
@app.route("/static/")
def _bloquear_listagem_pastas():
    """Ninguem consegue navegar/listar o conteudo das pastas - so acessar um
    arquivo especifico se souber o link exato dele."""
    return "Acesso negado.", 403


GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
_cliente = Groq(api_key=GROQ_API_KEY) if (Groq and GROQ_API_KEY) else None

# ---------- Varias IAs de texto gratuitas, em CORRIDA ----------
# Em vez de tentar uma, esperar falhar, tentar a proxima (o que soma o tempo de
# espera de cada uma), aqui a gente dispara todas as que tiverem chave configurada
# AO MESMO TEMPO numa thread cada, e fica com a primeira que responder - as outras
# sao descartadas. Isso deixa a resposta rapida mesmo se uma das IAs estiver lenta
# naquele momento, porque ela simplesmente perde a corrida em vez de travar tudo.
# Cada uma so entra em acao se a chave dela estiver configurada nas variaveis de
# ambiente do Render - sem chave, ela e simplesmente pulada (nao quebra nada).
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def _chat_groq(modelo, mensagens_completas):
    if not _cliente:
        raise RuntimeError("Groq nao configurado")
    resposta = _cliente.chat.completions.create(model=modelo, messages=mensagens_completas, max_tokens=500, timeout=15)
    return resposta.choices[0].message.content


def _chat_http_openai_compat(url, chave, modelo, mensagens_completas):
    """Varios provedores gratuitos (OpenRouter, Cerebras) falam o mesmo formato da
    OpenAI - reaproveita a mesma chamada HTTP pra todos."""
    if not chave or not requests:
        raise RuntimeError("provedor nao configurado")
    resposta = requests.post(
        url,
        headers={"Authorization": f"Bearer {chave}", "Content-Type": "application/json"},
        json={"model": modelo, "messages": mensagens_completas, "max_tokens": 500},
        timeout=15,
    )
    if resposta.status_code >= 400:
        raise RuntimeError(f"HTTP {resposta.status_code}: {resposta.text[:300]}")
    return resposta.json()["choices"][0]["message"]["content"]


def _chat_gemini(mensagens_completas):
    """Google Gemini (plano gratuito generoso, chave gratis em aistudio.google.com/apikey).
    A API do Gemini fala um formato proprio (nao e compativel com OpenAI), entao
    converte as mensagens no formato dela antes de mandar."""
    if not GEMINI_API_KEY or not requests:
        raise RuntimeError("Gemini nao configurado")
    sistema = " ".join(m["content"] for m in mensagens_completas if m.get("role") == "system")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in mensagens_completas if m.get("role") in ("user", "assistant")
    ]
    corpo = {"contents": contents}
    if sistema:
        corpo["system_instruction"] = {"parts": [{"text": sistema}]}
    resposta = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
        json=corpo,
        timeout=15,
    )
    if resposta.status_code >= 400:
        raise RuntimeError(f"HTTP {resposta.status_code}: {resposta.text[:300]}")
    return resposta.json()["candidates"][0]["content"]["parts"][0]["text"]


def gerar_resposta_ia(mensagens_completas):
    """Dispara todas as IAs de texto configuradas ao mesmo tempo e devolve a
    primeira que responder (corrida). Bem mais rapido que tentar uma de cada vez,
    porque o tempo total passa a ser o da mais rapida, nao a soma de todas."""
    provedores = [
        ("Groq (70b)", lambda: _chat_groq("openai/gpt-oss-120b", mensagens_completas)),
        ("Groq (8b, mais rapida)", lambda: _chat_groq("openai/gpt-oss-20b", mensagens_completas)),
        ("Cerebras", lambda: _chat_http_openai_compat(
            "https://api.cerebras.ai/v1/chat/completions", CEREBRAS_API_KEY, "gpt-oss-120b", mensagens_completas)),
        ("OpenRouter", lambda: _chat_http_openai_compat(
            "https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY,
            "meta-llama/llama-3.1-8b-instruct:free", mensagens_completas)),
        ("Gemini", lambda: _chat_gemini(mensagens_completas)),
    ]
    resultados = queue.Queue()

    def _rodar(nome, chamada):
        try:
            resultados.put(("ok", nome, chamada()))
        except Exception as erro:
            resultados.put(("erro", nome, erro))

    threads = [threading.Thread(target=_rodar, args=(nome, chamada), daemon=True) for nome, chamada in provedores]
    for t in threads:
        t.start()
    erros = []
    prazo_final = time.monotonic() + 17  # prazo TOTAL da corrida, nao por provedor - evita que varios
    # travamentos em sequencia somem e deixem a pessoa esperando muito mais que isso.
    for _ in range(len(provedores)):
        tempo_restante = prazo_final - time.monotonic()
        if tempo_restante <= 0:
            break
        try:
            status, nome, valor = resultados.get(timeout=tempo_restante)
        except queue.Empty:
            break
        if status == "ok":
            if erros:
                print(f"[CHAT CPA] '{nome}' venceu a corrida (outras ainda rodando/falharam: {[e[0] for e in erros]})")
            return valor
        erros.append((nome, valor))
        print(f"[CHAT CPA] provedor de texto '{nome}' falhou: {valor}")
    raise RuntimeError(f"Todas as IAs de texto falharam ou demoraram demais. Erros: {erros}")


# ---------- Varias IAs de imagem gratuitas, em cascata ----------
# Igual ao texto: se a Pollinations demorar demais ou cair, tenta o Hugging Face
# (precisa de HF_API_KEY gratuita em huggingface.co/settings/tokens).
HF_API_KEY = os.environ.get("HF_API_KEY", "")


def gerar_imagem_bytes(prompt_melhorado, seed):
    """Dispara Pollinations e Hugging Face (se configurado) ao mesmo tempo e fica
    com a primeira imagem valida que chegar - assim a demora vira a da mais rapida
    das duas, nao a soma (uma esperar a outra falhar/dar timeout pra so entao tentar
    a proxima)."""
    def _pollinations():
        url_pollinations = (
            "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt_melhorado)
            + f"?model=flux&width=1024&height=1024&seed={seed}&nologo=true"
        )
        resposta = requests.get(url_pollinations, timeout=25)
        if resposta.status_code == 200 and resposta.content and resposta.headers.get("content-type", "").startswith("image"):
            return resposta.content
        raise RuntimeError(f"Pollinations respondeu sem imagem valida (status {resposta.status_code})")

    def _hugging_face():
        if not HF_API_KEY:
            raise RuntimeError("Hugging Face nao configurado")
        resposta = requests.post(
            "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
            headers={"Authorization": f"Bearer {HF_API_KEY}"},
            json={"inputs": prompt_melhorado},
            timeout=30,
        )
        if resposta.status_code == 200 and resposta.headers.get("content-type", "").startswith("image"):
            return resposta.content
        raise RuntimeError(f"Hugging Face respondeu sem imagem valida (status {resposta.status_code})")

    if not requests:
        return None
    provedores = [("Pollinations", _pollinations), ("Hugging Face", _hugging_face)]
    resultados = queue.Queue()

    def _rodar(nome, chamada):
        try:
            resultados.put(("ok", nome, chamada()))
        except Exception as erro:
            resultados.put(("erro", nome, erro))

    threads = [threading.Thread(target=_rodar, args=(nome, chamada), daemon=True) for nome, chamada in provedores]
    for t in threads:
        t.start()
    prazo_final = time.monotonic() + 27
    for _ in range(len(provedores)):
        tempo_restante = prazo_final - time.monotonic()
        if tempo_restante <= 0:
            break
        try:
            status, nome, valor = resultados.get(timeout=tempo_restante)
        except queue.Empty:
            break
        if status == "ok":
            return valor
        print(f"[CHAT CPA] provedor de imagem '{nome}' falhou: {valor}")
    return None

SISTEMA = (
    "Voce e o CHAT CPA, assistente de IA criado por Samuca. "
    "Responda em portugues do Brasil, de forma clara e amigavel, curto e direto (as vezes a resposta sera falada em voz alta, entao evite listas longas). "
    "Voce NAO tem controle sobre nenhum computador, e apenas um assistente de conversa e criacao de imagens."
)

CAMINHO_BD = os.environ.get("CAMINHO_BD", "jarvis.db")
CONTA_DESENVOLVEDOR = "SAMUCA"
PIN_VERIFICACAO = "9090"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# ---------- Servidores ICE para as chamadas de audio/video do ZAP ----------
# So com STUN, muita gente atras de NAT "fechado" (comum em dados moveis/4G e em
# algumas redes de empresa) NUNCA consegue trocar midia com a outra ponta - a
# chamada conecta mas video/audio remoto nunca chega. Um servidor TURN resolve
# isso retransmitindo a midia. Configure TURN_URL / TURN_USUARIO / TURN_SENHA nas
# variaveis de ambiente (por exemplo com um servico como metered.ca, Twilio ou um
# coturn proprio) para deixar as chamadas confiaveis em qualquer rede.
TURN_URL = os.environ.get("TURN_URL", "")
TURN_USUARIO = os.environ.get("TURN_USUARIO", "")
TURN_SENHA = os.environ.get("TURN_SENHA", "")
_ICE_SERVERS = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
    {"urls": "stun:stun.cloudflare.com:3478"},
]
if TURN_URL:
    _entrada_turn = {"urls": TURN_URL}
    if TURN_USUARIO:
        _entrada_turn["username"] = TURN_USUARIO
    if TURN_SENHA:
        _entrada_turn["credential"] = TURN_SENHA
    _ICE_SERVERS.append(_entrada_turn)
ICE_SERVERS_JSON = json.dumps(_ICE_SERVERS)
PASTA_UPLOADS = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(PASTA_UPLOADS, exist_ok=True)

# ---------- Dono do site (fica sempre com o ID permanente 1) ----------
EMAIL_DONO = "samuelgomeswx2000@gmail.com"

# ---------- Fundo da tela inicial ----------
# Atencao: links do CDN do Discord (cdn.discordapp.com/attachments/...) expiram
# depois de um tempo (veja os parametros ?ex=...&is=...&hm=... na URL). Quando
# esse link parar de funcionar, e so trocar o valor abaixo por outro link de
# imagem publica (ou subir a imagem para /static e usar "/static/fundo.jpg").
FUNDO_INICIO_URL = "https://cdn.discordapp.com/attachments/1527396622336524359/1538287118911021250/1f38792ffa63762e59c32946e314626e.jpg?ex=6a822105&is=6a80cf85&hm=ac3dc688febe05554a4409abeffc4e5c54b9cbf3427d3ac5ce77f8298bf80cb5&"

# ---------- ImgBB (hospedagem de imagens de perfil/posts) ----------
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")
IMGBB_URL = "https://api.imgbb.com/1/upload"

# ---------- Envio de codigo por email (login sem senha) ----------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USUARIO = os.environ.get("SMTP_USUARIO", "")
SMTP_SENHA = os.environ.get("SMTP_SENHA", "")
SMTP_REMETENTE = os.environ.get("SMTP_REMETENTE", SMTP_USUARIO)

# Envio de email via Resend (API HTTP - nao depende de porta SMTP, que o Render
# costuma bloquear no plano free). Se RESEND_API_KEY estiver configurada, ela
# tem prioridade sobre o SMTP. RESEND_REMETENTE pode ficar em branco pra usar
# o dominio de teste do Resend (onboarding@resend.dev).
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_REMETENTE = os.environ.get("RESEND_REMETENTE", "CHAT CPA <onboarding@resend.dev>")


def email_esta_configurado():
    """True se existir algum jeito de mandar email (Resend ou SMTP)."""
    return bool(RESEND_API_KEY) or bool(SMTP_HOST and SMTP_USUARIO and SMTP_SENHA)
MINUTOS_VALIDADE_CODIGO = 10


def obter_bd():
    conexao = sqlite3.connect(CAMINHO_BD)
    conexao.row_factory = sqlite3.Row
    return conexao


def iniciar_bd():
    conexao = obter_bd()
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            usuario TEXT PRIMARY KEY, senha_hash TEXT NOT NULL,
            verificado INTEGER DEFAULT 0, foto_perfil TEXT, banner TEXT,
            bio TEXT, email TEXT, tag TEXT
        )
    """)
    for coluna, tipo in [
        ("verificado", "INTEGER DEFAULT 0"), ("foto_perfil", "TEXT"), ("banner", "TEXT"),
        ("bio", "TEXT"), ("email", "TEXT"), ("tag", "TEXT"),
        ("id_publico", "INTEGER"), ("data_nascimento", "TEXT"), ("viu_boas_vindas", "INTEGER DEFAULT 0"),
        ("ultima_atividade", "TEXT"), ("bloqueado", "INTEGER DEFAULT 0"),
        ("avisos_moderacao", "INTEGER DEFAULT 0"),
    ]:
        try:
            conexao.execute(f"ALTER TABLE usuarios ADD COLUMN {coluna} {tipo}")
        except sqlite3.OperationalError:
            pass
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS codigos_verificacao (
            email TEXT PRIMARY KEY, codigo TEXT NOT NULL, criado_em TEXT NOT NULL
        )
    """)
    try:
        conexao.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_id_publico ON usuarios(id_publico)")
    except sqlite3.OperationalError:
        pass
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT NOT NULL,
            remetente TEXT NOT NULL, texto TEXT NOT NULL, criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT NOT NULL,
            texto TEXT, imagem TEXT, video TEXT, criado_em TEXT NOT NULL
        )
    """)
    try:
        conexao.execute("ALTER TABLE posts ADD COLUMN video TEXT")
    except sqlite3.OperationalError:
        pass
    conexao.execute("CREATE TABLE IF NOT EXISTS curtidas (post_id INTEGER NOT NULL, usuario TEXT NOT NULL, PRIMARY KEY (post_id, usuario))")
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS comentarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL,
            usuario TEXT NOT NULL, texto TEXT NOT NULL, criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("CREATE TABLE IF NOT EXISTS seguidores (seguidor TEXT NOT NULL, seguido TEXT NOT NULL, PRIMARY KEY (seguidor, seguido))")
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            nome TEXT PRIMARY KEY, cor TEXT NOT NULL, foto TEXT
        )
    """)
    # ---------- ZAP ----------
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_contatos (
            usuario TEXT NOT NULL, contato TEXT NOT NULL, criado_em TEXT NOT NULL,
            PRIMARY KEY (usuario, contato)
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversa TEXT NOT NULL,
            remetente TEXT NOT NULL, destinatario TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'texto', conteudo TEXT NOT NULL,
            criptografado INTEGER DEFAULT 0, criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_conversas (
            conversa TEXT PRIMARY KEY, frase_cripto TEXT, ativada_por TEXT, criado_em TEXT
        )
    """)
    conexao.execute("CREATE TABLE IF NOT EXISTS zap_denuncias (id INTEGER PRIMARY KEY AUTOINCREMENT, mensagem_id INTEGER NOT NULL, denunciante TEXT NOT NULL, criado_em TEXT NOT NULL)")
    # ---------- Grupos do ZAP ----------
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_grupos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, foto TEXT,
            criado_por TEXT NOT NULL, verificado INTEGER DEFAULT 0, criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_grupo_membros (
            grupo_id INTEGER NOT NULL, usuario TEXT NOT NULL, admin INTEGER DEFAULT 0,
            PRIMARY KEY (grupo_id, usuario)
        )
    """)
    try:
        conexao.execute("ALTER TABLE zap_grupo_membros ADD COLUMN admin INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_grupo_mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, grupo_id INTEGER NOT NULL,
            remetente TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'texto', conteudo TEXT NOT NULL,
            criado_em TEXT NOT NULL
        )
    """)
    # ---------- Salvos (posts salvos pelo usuario no feed) ----------
    conexao.execute("CREATE TABLE IF NOT EXISTS salvos (usuario TEXT NOT NULL, post_id INTEGER NOT NULL, criado_em TEXT NOT NULL, PRIMARY KEY (usuario, post_id))")
    # ---------- Bloqueios individuais no ZAP (diferente do bloqueio global por moderacao) ----------
    conexao.execute("CREATE TABLE IF NOT EXISTS zap_bloqueios (usuario TEXT NOT NULL, bloqueado TEXT NOT NULL, criado_em TEXT NOT NULL, PRIMARY KEY (usuario, bloqueado))")
    # ---------- Configuracoes gerais (icones dos apps, selo customizado, etc) ----------
    conexao.execute("CREATE TABLE IF NOT EXISTS configuracoes (chave TEXT PRIMARY KEY, valor TEXT)")
    # ---------- Ligacoes de voz do ZAP (sinalizacao WebRTC via polling) ----------
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_chamadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, quem_liga TEXT NOT NULL, quem_recebe TEXT NOT NULL,
            oferta TEXT, resposta TEXT, candidatos_liga TEXT DEFAULT '[]', candidatos_recebe TEXT DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'chamando', criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("CREATE TABLE IF NOT EXISTS agentes_suporte (usuario TEXT PRIMARY KEY)")
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS tickets_suporte (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT NOT NULL,
            atendente TEXT, status TEXT DEFAULT 'aberto', criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS mensagens_suporte (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
            remetente TEXT NOT NULL, texto TEXT NOT NULL, criado_em TEXT NOT NULL
        )
    """)
    existe_dev_tag = conexao.execute("SELECT 1 FROM tags WHERE nome = 'DEV'").fetchone()
    if not existe_dev_tag:
        conexao.execute("INSERT INTO tags (nome, cor, foto) VALUES ('DEV', '#ffffff', NULL)")
    conexao.execute("UPDATE usuarios SET tag = 'DEV' WHERE usuario = ? COLLATE NOCASE AND tag IS NULL", (CONTA_DESENVOLVEDOR,))
    # garante que o dono do site sempre fique com o ID permanente 1
    conexao.execute(
        "UPDATE usuarios SET id_publico = 1 WHERE email = ? COLLATE NOCASE AND (id_publico IS NULL OR id_publico != 1)",
        (EMAIL_DONO,),
    )
    conexao.commit()
    # preenche id_publico para contas antigas que ainda nao tem um (compatibilidade)
    sem_id = conexao.execute("SELECT usuario FROM usuarios WHERE id_publico IS NULL").fetchall()
    for linha in sem_id:
        novo_id = gerar_id_publico(conexao)
        conexao.execute("UPDATE usuarios SET id_publico = ? WHERE usuario = ?", (novo_id, linha["usuario"]))
    conexao.commit()
    conexao.close()


def gerar_id_publico(conexao):
    """IDs de 2 a 11 sao reservados (so o dono atribui manualmente pelo painel).
    A partir do 12, todo mundo recebe automaticamente em ordem crescente."""
    maior = conexao.execute("SELECT MAX(id_publico) as m FROM usuarios WHERE id_publico >= 12").fetchone()["m"]
    return (maior or 11) + 1


iniciar_bd()


def buscar_usuario(nome):
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM usuarios WHERE usuario = ? COLLATE NOCASE", (nome,)).fetchone()
    conexao.close()
    return linha


def eh_desenvolvedor(nome_usuario):
    """Verifica se quem esta logado e o dono do site. Antes so funcionava se o
    apelido fosse exatamente 'SAMUCA'; agora tambem reconhece pelo EMAIL_DONO,
    entao a conta continua sendo dev mesmo com outro apelido."""
    if not nome_usuario:
        return False
    if nome_usuario.upper() == CONTA_DESENVOLVEDOR:
        return True
    linha = buscar_usuario(nome_usuario)
    return bool(linha and linha["email"] and linha["email"].strip().lower() == EMAIL_DONO.lower())


def salvar_bytes_imagem(conteudo, extensao="jpg"):
    """Salva bytes de imagem (ja gerados/baixados) direto no disco local e devolve
    a URL. Usado pelas imagens geradas por IA, que ja chegam como bytes prontos."""
    nome_unico = f"{uuid.uuid4().hex}.{extensao}"
    with open(os.path.join(PASTA_UPLOADS, nome_unico), "wb") as f:
        f.write(conteudo)
    return f"/static/uploads/{nome_unico}"


def salvar_arquivo_enviado(arquivo):
    """Fallback local: usado apenas se o ImgBB nao estiver configurado ou falhar."""
    if not arquivo or not arquivo.filename:
        return None
    nome_seguro = secure_filename(arquivo.filename)
    nome_unico = f"{uuid.uuid4().hex}_{nome_seguro}"
    arquivo.save(os.path.join(PASTA_UPLOADS, nome_unico))
    return f"/static/uploads/{nome_unico}"


def salvar_imagem(arquivo):
    """Hospeda a imagem (avatar, banner ou post) no ImgBB. Se a chave nao estiver
    configurada ou o upload falhar, cai para armazenamento local como reserva."""
    if not arquivo or not arquivo.filename:
        return None
    if IMGBB_API_KEY and requests:
        try:
            conteudo = arquivo.read()
            imagem_b64 = base64.b64encode(conteudo).decode("utf-8")
            resposta = requests.post(
                IMGBB_URL,
                data={"key": IMGBB_API_KEY, "image": imagem_b64},
                timeout=20,
            )
            dados = resposta.json()
            if dados.get("success"):
                return dados["data"]["url"]
        except Exception:
            pass
        arquivo.seek(0)
    return salvar_arquivo_enviado(arquivo)


def gerar_codigo():
    return "".join(secrets.choice("0123456789") for _ in range(6))


def _enviar_email_via_resend(email, assunto, corpo):
    resposta = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": RESEND_REMETENTE,
            "to": [email],
            "subject": assunto,
            "text": corpo,
        },
        timeout=10,
    )
    if resposta.status_code >= 400:
        raise Exception(f"Resend respondeu {resposta.status_code}: {resposta.text[:300]}")
    return True


def enviar_email_codigo(email, codigo):
    """Envia o codigo de login por email. Tenta primeiro via Resend (API HTTP,
    nao bloqueada pelo Render); se nao estiver configurado, cai pro SMTP; se
    nenhum dos dois estiver configurado, grava o codigo no console."""
    assunto = "Seu codigo de acesso - CHAT CPA"
    corpo = f"Seu codigo de verificacao e: {codigo}\n\nEle expira em {MINUTOS_VALIDADE_CODIGO} minutos.\nSe voce nao pediu esse codigo, ignore este email."

    if RESEND_API_KEY and requests is not None:
        try:
            return _enviar_email_via_resend(email, assunto, corpo)
        except Exception as erro:
            print(f"[CHAT CPA] falha ao enviar email via Resend para {email}: {erro}")
            # cai pro SMTP abaixo se tiver configurado, em vez de desistir na hora

    if not (SMTP_HOST and SMTP_USUARIO and SMTP_SENHA):
        if not RESEND_API_KEY:
            # Nem Resend nem SMTP configurados (ambiente de teste/local).
            # Antes isso fingia sucesso e deixava a pessoa travada, porque o codigo so
            # aparecia no log do servidor, que ninguem alem do dono consegue ver.
            # Agora avisamos a rota chamadora para que o codigo seja mostrado na tela.
            print(f"[CHAT CPA] (email nao configurado) codigo para {email}: {codigo}")
            return "sem_smtp"
        return False
    try:
        mensagem = MIMEText(corpo)
        mensagem["Subject"] = assunto
        mensagem["From"] = SMTP_REMETENTE
        mensagem["To"] = email
        contexto = ssl.create_default_context()
        # timeout curto: se o provedor de email estiver lento/travado, falha rapido
        # em vez de deixar a pessoa esperando o codigo por muito tempo na tela de login.
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=contexto, timeout=10) as servidor:
            servidor.login(SMTP_USUARIO, SMTP_SENHA)
            servidor.sendmail(SMTP_REMETENTE, [email], mensagem.as_string())
        return True
    except Exception as erro:
        print(f"[CHAT CPA] falha ao enviar email para {email}: {erro}")
        return False


def enviar_email_codigo_async(email, codigo):
    """Dispara o envio do email totalmente em segundo plano (sem esperar nada).
    Mantida para uso futuro; a rota de login usa uma versao com espera curta,
    ver auth_enviar_codigo."""
    threading.Thread(target=enviar_email_codigo, args=(email, codigo), daemon=True).start()


def criar_usuario(usuario, email, foto_perfil=None, banner=None, bio=None, data_nascimento=None, id_publico=None):
    conexao = obter_bd()
    if id_publico is None:
        id_publico = 1 if email.strip().lower() == EMAIL_DONO.lower() else gerar_id_publico(conexao)
    senha_aleatoria = generate_password_hash(uuid.uuid4().hex)
    conexao.execute(
        """INSERT INTO usuarios (usuario, senha_hash, verificado, email, foto_perfil, banner, bio, data_nascimento, id_publico)
           VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)""",
        (usuario, senha_aleatoria, email, foto_perfil, banner, bio, data_nascimento, id_publico),
    )
    conexao.commit()
    conexao.close()
    return usuario


def html_tag(nome_tag):
    if not nome_tag:
        return ""
    conexao = obter_bd()
    linha = conexao.execute("SELECT cor, foto FROM tags WHERE nome = ?", (nome_tag,)).fetchone()
    conexao.close()
    if not linha:
        return ""
    foto_html = f'<img src="{linha["foto"]}">' if linha["foto"] else ""
    return f'<span class="tag-badge" style="background:{linha["cor"]}">{foto_html}{nome_tag}</span>'


# ================= ZAP: presenca online, criptografia e moderacao =================

MINUTOS_CONSIDERADO_ONLINE = 3


def marcar_atividade(usuario):
    """Atualiza o horario da ultima atividade do usuario (usado para contar quem esta online)."""
    if not usuario:
        return
    conexao = obter_bd()
    conexao.execute("UPDATE usuarios SET ultima_atividade = ? WHERE usuario = ? COLLATE NOCASE", (datetime.now().isoformat(), usuario))
    conexao.commit()
    conexao.close()


def contar_online():
    limite = (datetime.now() - timedelta(minutes=MINUTOS_CONSIDERADO_ONLINE)).isoformat()
    conexao = obter_bd()
    linha = conexao.execute("SELECT COUNT(*) AS n FROM usuarios WHERE ultima_atividade IS NOT NULL AND ultima_atividade >= ?", (limite,)).fetchone()
    conexao.close()
    return linha["n"] if linha else 0


def contar_contas():
    conexao = obter_bd()
    linha = conexao.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()
    conexao.close()
    return linha["n"] if linha else 0


def id_conversa(usuario_a, usuario_b):
    """Identificador estavel (e igual dos dois lados) de uma conversa do ZAP entre duas contas."""
    return "|".join(sorted([usuario_a.lower(), usuario_b.lower()]))


def buscar_usuario_por_id_publico(id_publico):
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM usuarios WHERE id_publico = ?", (id_publico,)).fetchone()
    conexao.close()
    return linha


def buscar_usuario_por_email_ou_id(valor):
    """Usado nos paineis de admin: aceita tanto um email quanto um ID publico (com ou sem #)."""
    valor = (valor or "").strip()
    if not valor:
        return None
    conexao = obter_bd()
    if "@" in valor:
        linha = conexao.execute("SELECT * FROM usuarios WHERE email = ? COLLATE NOCASE", (valor,)).fetchone()
    else:
        try:
            id_num = int(valor.lstrip("#"))
            linha = conexao.execute("SELECT * FROM usuarios WHERE id_publico = ?", (id_num,)).fetchone()
        except ValueError:
            linha = None
    conexao.close()
    return linha


def obter_config(chave, padrao=None):
    conexao = obter_bd()
    linha = conexao.execute("SELECT valor FROM configuracoes WHERE chave = ?", (chave,)).fetchone()
    conexao.close()
    return linha["valor"] if linha and linha["valor"] else padrao


def definir_config(chave, valor):
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO configuracoes (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, valor),
    )
    conexao.commit()
    conexao.close()


def salvar_midia_zap(arquivo):
    """Envia imagens para o ImgBB; audios e videos ficam salvos localmente (o ImgBB so aceita imagem)."""
    if not arquivo or not arquivo.filename:
        return None
    tipo = (arquivo.mimetype or "").lower()
    if tipo.startswith("image/"):
        return salvar_imagem(arquivo)
    return salvar_arquivo_enviado(arquivo)


def chave_a_partir_da_frase(frase):
    """Deriva uma chave Fernet (AES) a partir de uma frase/data escolhida pela pessoa,
    por exemplo 'criptografia de 15/08/2000'. A mesma frase sempre gera a mesma chave."""
    if not Fernet:
        return None
    sal = b"jarviszap-sal-fixo"
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sal, iterations=390000)
    chave = _b64.urlsafe_b64encode(kdf.derive(frase.strip().lower().encode("utf-8")))
    return chave


def criptografar_texto(texto, frase):
    if not Fernet or not frase:
        return texto, False
    try:
        f = Fernet(chave_a_partir_da_frase(frase))
        return f.encrypt(texto.encode("utf-8")).decode("utf-8"), True
    except Exception:
        return texto, False


def descriptografar_texto(texto_cifrado, frase):
    if not Fernet or not frase:
        return None
    try:
        f = Fernet(chave_a_partir_da_frase(frase))
        return f.decrypt(texto_cifrado.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return None


# Lista de termos usada pelo bot do CHAT CPA para barrar mensagens de texto com
# conteudo adulto/sexual explicito ou de terror/ameaca grave. E uma checagem
# simples por palavra-chave (nao substitui moderacao humana nem analisa fotos/
# audios/videos, que exigiriam um servico externo de analise de midia).
TERMOS_PROIBIDOS = [
    "pornografia", "porno", "nudes", "sexo explicito", "conteudo adulto",
    "estupro", "pedofilia", "suicidio assistido", "como matar", "como se matar",
]


def mensagem_contem_conteudo_proibido(texto):
    texto_normalizado = (texto or "").lower()
    return any(termo in texto_normalizado for termo in TERMOS_PROIBIDOS)


def aplicar_moderacao(usuario):
    """Registra um aviso de moderacao para a conta e bloqueia apos repetidas violacoes."""
    conexao = obter_bd()
    conexao.execute("UPDATE usuarios SET avisos_moderacao = COALESCE(avisos_moderacao, 0) + 1 WHERE usuario = ? COLLATE NOCASE", (usuario,))
    linha = conexao.execute("SELECT avisos_moderacao FROM usuarios WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchone()
    avisos = linha["avisos_moderacao"] if linha else 1
    bloqueado = False
    if avisos >= 3:
        conexao.execute("UPDATE usuarios SET bloqueado = 1 WHERE usuario = ? COLLATE NOCASE", (usuario,))
        bloqueado = True
    conexao.commit()
    conexao.close()
    return avisos, bloqueado


ICONE_MIC = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 14a3 3 0 003-3V6a3 3 0 00-6 0v5a3 3 0 003 3zm5-3a5 5 0 01-10 0H5a7 7 0 006 6.92V21h2v-3.08A7 7 0 0019 11h-2z"/></svg>"""
ICONE_MIC_OFF = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 11h-2a5 5 0 01-8.11 3.91L7.5 16.3A7 7 0 0017 11h2zM4.27 3L3 4.27l6 6V11a3 3 0 003 3c.2 0 .38-.03.56-.06l1.55 1.55A5 5 0 0112 9v.73L19.73 21 21 19.73 4.27 3z"/></svg>"""
ICONE_ONDA = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M3 10v4h3l4 4V6l-4 4H3zm10.5 2a4.5 4.5 0 00-2.5-4.03v8.06A4.5 4.5 0 0013.5 12zM13 3.23v2.06c3.39.87 5.5 4.24 4.63 7.63A6.98 6.98 0 0113 17.71v2.06c4.5-.93 7.44-5.33 6.51-9.83A8.02 8.02 0 0013 3.23z"/></svg>"""
SELO_VERIFICADO = """<svg viewBox="0 0 24 24" width="14" height="14" fill="#fff" style="vertical-align:middle;margin-left:3px;"><path d="M12 2l2.4 2.4 3.3-.5.8 3.3 3.1 1.4-1.1 3.2 1.1 3.2-3.1 1.4-.8 3.3-3.3-.5L12 22l-2.4-2.4-3.3.5-.8-3.3-3.1-1.4 1.1-3.2-1.1-3.2 3.1-1.4.8-3.3 3.3.5z"/><path d="M9.5 12.5l1.8 1.8 3.2-4" stroke="#000" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>"""


def selo_verificado_html(tamanho=14):
    """Selo de verificado: usa a imagem configurada pelo dono no painel (se houver),
    senao cai no SVG padrao."""
    imagem_customizada = obter_config("selo_verificado_url")
    if imagem_customizada:
        return f'<img src="{imagem_customizada}" style="width:{tamanho}px;height:{tamanho}px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-left:3px;">'
    if tamanho == 14:
        return SELO_VERIFICADO
    return SELO_VERIFICADO.replace('width="14" height="14"', f'width="{tamanho}" height="{tamanho}"')


AVATAR_PADRAO = "https://api.dicebear.com/7.x/identicon/svg?seed="

ESTILO_COMUM = """
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { height:100%; max-width:100%; overflow-x:hidden; }
body { margin:0; font-family: 'Segoe UI', Arial, sans-serif; background:#000000; color:#f2f2f2; }
img, video, svg { max-width:100%; }
button, .acao-lateral, .nav-inferior, .botao-seguir-lateral, .tag-badge, .item-icone, .painel-admin-abas button,
.linha-membro-grupo button, .item-grupo, .voltar, .titulo-topo, .botao-engrenagem, .botao-info-grupo {
  -webkit-user-select:none; -moz-user-select:none; user-select:none;
}
/* Em telas grandes (PC), impede que o conteudo principal fique esticado/cortado
   de um lado a outro do monitor: centraliza um miolo com largura maxima confortavel. */
@media (min-width: 900px) {
  .container { max-width: 900px; margin-left:auto; margin-right:auto; }
}
.tag-badge { display:inline-flex; align-items:center; gap:4px; font-size:10px; padding:2px 7px; border-radius:8px; font-weight:bold; color:#000; vertical-align:middle; margin-left:4px; }
.tag-badge img { width:12px; height:12px; border-radius:50%; object-fit:cover; }
"""

# ---------- SPLASH (tela de carregando ao entrar / cadastrar) ----------
# ---------- BOAS-VINDAS CINEMATOGRAFICA (so na primeira vez que a pessoa entra) ----------
# Imagem de fundo da tela de boas-vindas, guardada em pedacos pequenos
# (uma linha unica gigantesca corrompe facilmente ao colar/hospedar o codigo)
_FUNDO_BOAS_VINDAS_B64 = "".join([
    "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"
])


PAGINA_BOAS_VINDAS = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CHAT CPA</title>
<style>
  :root{
    --bg:#03060c;
    --cyan:#3ddc6a;
    --cyan-dim:#1f8fae;
    --blue:#2b6fff;
    --ink:#eaf7ff;
  }
  *{box-sizing:border-box; margin:0; padding:0;}
  html,body{height:100%;}
  body{
    color:var(--ink);
    font-family:'Rajdhani','Segoe UI',sans-serif;
    overflow:hidden;
    display:flex; align-items:center; justify-content:center;
    height:100vh;
    background-color:var(--bg);
    background-image:
      radial-gradient(ellipse at 50% 42%, rgba(10,22,38,.55) 0%, rgba(3,6,12,.85) 55%, rgba(3,6,12,.97) 100%),
      url('data:image/jpeg;base64,{FUNDO_BOAS_VINDAS_B64}');
    background-size:cover, cover;
    background-position:center, center;
    background-attachment:fixed, fixed;
  }
  .grid-overlay{
    position:fixed; inset:0; pointer-events:none; z-index:1;
    background-image:
      linear-gradient(rgba(91,230,255,.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(91,230,255,.035) 1px, transparent 1px);
    background-size:42px 42px;
    mask-image:radial-gradient(circle at 50% 50%, black 0%, transparent 72%);
  }
  .scanline{
    position:fixed; left:0; right:0; height:2px; z-index:6; pointer-events:none;
    background:linear-gradient(90deg, transparent, rgba(91,230,255,.5), transparent);
    animation:scan 5s linear infinite; opacity:.5;
  }
  @keyframes scan{ 0%{top:-2%;} 100%{top:102%;} }
  .stage{
    position:fixed; inset:0;
    display:flex; align-items:center; justify-content:center;
    z-index:2;
    animation:driftForward 9s linear infinite;
  }
  @keyframes driftForward{ 0%{ transform:scale(1); } 100%{ transform:scale(1.18); } }
  .ring{ position:absolute; border-radius:50%; border:1px solid rgba(91,230,255,.16); }
  .ring.r1{ width:70vmin; height:70vmin; animation:spin 40s linear infinite; }
  .ring.r2{ width:52vmin; height:52vmin; border-style:dashed; border-color:rgba(43,111,255,.22); animation:spin 26s linear infinite reverse; }
  .ring.r3{ width:36vmin; height:36vmin; border-color:rgba(91,230,255,.24); animation:spin 18s linear infinite; }
  @keyframes spin{ to{ transform:rotate(360deg); } }
  .particles{ position:fixed; inset:0; z-index:2; }
  .particle{
    position:absolute; width:3px; height:3px; border-radius:50%;
    background:var(--cyan); box-shadow:0 0 8px rgba(91,230,255,.9);
    animation:driftp linear infinite; opacity:0;
  }
  @keyframes driftp{
    0%{ transform:translate(0,0); opacity:0; }
    12%{ opacity:.8; } 88%{ opacity:.5; }
    100%{ transform:translate(var(--dx),var(--dy)); opacity:0; }
  }
  .layer{
    position:fixed; inset:0; z-index:4;
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    text-align:center; padding:0 8vw;
    opacity:0; pointer-events:none;
    transform:scale(.88);
    transition:opacity .5s ease, transform .6s cubic-bezier(.2,.7,.2,1);
  }
  .layer.in{ opacity:1; transform:scale(1); }
  .layer.out{ opacity:0; transform:scale(1.3); transition:opacity .4s ease, transform .5s cubic-bezier(.4,0,1,1); }
  .core-wrap{ position:relative; width:min(38vw,220px); height:min(38vw,220px); margin:0 auto 22px; }
  .core-glow{
    position:absolute; inset:-40%; border-radius:50%;
    background:radial-gradient(circle, rgba(91,230,255,.35), rgba(43,111,255,.08) 55%, transparent 72%);
    filter:blur(2px); animation:pulse 3.2s ease-in-out infinite;
  }
  @keyframes pulse{ 0%,100%{ transform:scale(.92); opacity:.65;} 50%{ transform:scale(1.05); opacity:1;} }
  .core{
    position:relative; width:100%; height:100%; border-radius:50%;
    background:radial-gradient(circle at 35% 30%, #123246, #050c14 70%);
    border:1px solid rgba(91,230,255,.5);
    box-shadow:inset 0 0 30px rgba(91,230,255,.25), 0 0 24px rgba(91,230,255,.55);
    display:flex; align-items:center; justify-content:center;
  }
  .core::before{
    content:""; position:absolute; inset:14%; border-radius:50%;
    border:1px solid rgba(91,230,255,.3); animation:spin 9s linear infinite;
  }
  .core-mark{
    font-family:'Orbitron', sans-serif; font-size:clamp(10px,2vw,13px);
    letter-spacing:.35em; color:var(--cyan); text-shadow:0 0 10px rgba(91,230,255,.8);
  }
  .wordmark h1{
    font-family:'Orbitron', sans-serif; font-weight:700;
    font-size:clamp(28px,7vw,50px); letter-spacing:.26em; color:var(--ink);
    text-shadow:0 0 18px rgba(91,230,255,.55), 0 0 44px rgba(43,111,255,.25);
  }
  .wordmark .sub{
    margin-top:8px; font-size:clamp(10px,1.6vw,13px); letter-spacing:.5em;
    color:var(--cyan-dim); text-transform:uppercase;
  }
  .msg{
    font-family:'Orbitron', sans-serif;
    font-size:clamp(18px,3.4vw,30px);
    letter-spacing:.06em; line-height:1.5; color:var(--ink);
    text-shadow:0 0 16px rgba(91,230,255,.4);
    max-width:680px;
  }
  .msg .eyebrow{
    display:block; font-family:'Share Tech Mono', monospace;
    font-size:clamp(10px,1.5vw,12px); letter-spacing:.4em; color:var(--cyan-dim);
    text-transform:uppercase; margin-bottom:16px;
  }
  .terminal{
    font-family:'Share Tech Mono', monospace;
    font-size:clamp(20px,4vw,34px); color:var(--cyan);
    text-shadow:0 0 14px rgba(91,230,255,.6);
    letter-spacing:.02em; min-height:1.4em;
  }
  .cursor{ display:inline-block; width:.55ch; margin-left:2px; animation:blink 1s step-start infinite; }
  @keyframes blink{ 50%{ opacity:0; } }
  .welcome h2{
    font-family:'Orbitron', sans-serif; font-weight:700;
    font-size:clamp(24px,5.6vw,42px); letter-spacing:.08em; color:var(--ink);
    text-shadow:0 0 22px rgba(91,230,255,.65), 0 0 50px rgba(43,111,255,.3);
  }
  .welcome .sub{
    margin-top:14px; font-family:'Rajdhani',sans-serif; font-weight:500;
    font-size:clamp(13px,2vw,17px); letter-spacing:.08em; color:var(--cyan-dim);
  }
  .flare{
    position:fixed; inset:0; z-index:3; pointer-events:none;
    background:radial-gradient(circle at 50% 46%, rgba(91,230,255,0) 0%, transparent 60%);
    opacity:0; transition:opacity .8s ease;
  }
  .flare.on{ opacity:1; background:radial-gradient(circle at 50% 46%, rgba(91,230,255,.25) 0%, transparent 62%); }
  .pular{ position:fixed; bottom:22px; right:22px; z-index:10; background:#00000066; border:1px solid #ffffff33; color:#aaa; font-size:12px; padding:8px 14px; border-radius:20px; cursor:pointer; letter-spacing:1px; }
</style>
</head>
<body>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700&family=Rajdhani:wght@400;500&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<div class="grid-overlay"></div>
<div class="scanline"></div>
<div class="flare" id="flare"></div>
<div class="stage"><div class="ring r1"></div><div class="ring r2"></div><div class="ring r3"></div></div>
<div class="particles" id="particles"></div>
<div class="pular" onclick="window.location.href=\'/inicio\'">Pular &#8594;</div>

<div class="layer" id="cena1">
  <div class="core-wrap"><div class="core-glow"></div><div class="core"><div class="core-mark">CHAT CPA</div></div></div>
  <div class="wordmark"><h1>CHAT CPA</h1><div class="sub">Inteligencia artificial</div></div>
</div>
<div class="layer" id="cena2">
  <div class="msg"><span class="eyebrow">Origem</span>Criado por Samuca, para explorar ate onde a inteligencia artificial pode chegar.</div>
</div>
<div class="layer" id="cena3">
  <div class="msg"><span class="eyebrow">Proposito</span>Um espaco para pensar, criar e resolver - ao seu lado, sempre.</div>
</div>
<div class="layer" id="cena4">
  <div class="msg"><span class="eyebrow">Nucleo neural</span>Cada resposta nasce de uma rede que nunca para de aprender.</div>
</div>
<div class="layer" id="cena5">
  <div class="msg"><span class="eyebrow">Conexao segura</span>Agora, sincronizando com voce.</div>
</div>
<div class="layer" id="cena6">
  <div class="terminal"><span id="textoDigitado"></span><span class="cursor">|</span></div>
</div>
<div class="layer welcome" id="cena7">
  <h2>Ola, {nome}!</h2>
  <div class="sub">CHAT CPA esta pronto para voce.</div>
</div>

<script>
  const pWrap = document.getElementById(\'particles\');
  for (let i = 0; i < 24; i++) {
    const p = document.createElement(\'div\');
    p.className = \'particle\';
    const angle = Math.random() * Math.PI * 2;
    const radius = 30 + Math.random() * 55;
    p.style.setProperty(\'--dx\', Math.cos(angle) * radius + \'vw\');
    p.style.setProperty(\'--dy\', Math.sin(angle) * radius + \'vh\');
    p.style.left = (46 + Math.random() * 8) + \'vw\';
    p.style.top = (46 + Math.random() * 8) + \'vh\';
    p.style.animationDuration = (4 + Math.random() * 5) + \'s\';
    p.style.animationDelay = (Math.random() * 5) + \'s\';
    pWrap.appendChild(p);
  }
  function mostrar(id) { const el = document.getElementById(id); el.classList.remove(\'out\'); el.classList.add(\'in\'); }
  function avancar(id) { const el = document.getElementById(id); el.classList.remove(\'in\'); el.classList.add(\'out\'); }

  mostrar(\'cena1\');
  setTimeout(() => avancar(\'cena1\'), 1000);
  setTimeout(() => mostrar(\'cena2\'), 1200);
  setTimeout(() => avancar(\'cena2\'), 3000);
  setTimeout(() => mostrar(\'cena3\'), 3200);
  setTimeout(() => avancar(\'cena3\'), 5000);
  setTimeout(() => mostrar(\'cena4\'), 5200);
  setTimeout(() => avancar(\'cena4\'), 7000);
  setTimeout(() => mostrar(\'cena5\'), 7200);
  setTimeout(() => avancar(\'cena5\'), 8800);
  setTimeout(() => mostrar(\'cena6\'), 9000);

  const textoParaDigitar = \'Ola, {nome}\';
  setTimeout(() => {
    const alvo = document.getElementById(\'textoDigitado\');
    let i = 0;
    const intervalo = setInterval(() => {
      alvo.textContent += textoParaDigitar[i];
      i++;
      if (i >= textoParaDigitar.length) clearInterval(intervalo);
    }, 55);
  }, 9300);

  setTimeout(() => avancar(\'cena6\'), 11300);
  setTimeout(() => { mostrar(\'cena7\'); document.getElementById(\'flare\').classList.add(\'on\'); }, 11500);
  setTimeout(() => { window.location.href = \'/inicio\'; }, 14200);
</script>
</body></html>
"""


PAGINA_CARREGANDO = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>CHAT CPA</title>
<style>
""" + ESTILO_COMUM + """
body { height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; padding:0 30px; }
.logo-hud { width:110px; height:110px; position:relative; margin-bottom:24px; }
.anel { position:absolute; border-radius:50%; border:2px solid #ffffff; opacity:0.85; }
.anel1 { inset:0; animation: girar 6s linear infinite; border-color:#3ddc6a; box-shadow:0 0 14px #3ddc6a55; }
.anel2 { inset:14px; border-color:#cccccc; animation: girar 4s linear infinite reverse; }
.anel3 { inset:30px; border-color:#999999; animation: girar 3s linear infinite; }
@keyframes girar { from { transform: rotate(0deg);} to { transform: rotate(360deg);} }
h1 { letter-spacing:4px; font-size:22px; margin:0; }
.dica-ia { margin-top:26px; max-width:300px; text-align:center; color:#aaa; font-size:13px; line-height:1.5; min-height:40px; animation: aparecerDica 0.4s ease; }
@keyframes aparecerDica { from { opacity:0; transform:translateY(4px);} to { opacity:1; transform:translateY(0);} }
.dica-ia b { color:#fff; }
.credito { position:absolute; bottom:30px; color:#666666; font-size:12px; letter-spacing:1px; opacity:0.55; font-weight:300; }
</style></head>
<body>
<div class="logo-hud"><div class="anel anel1"></div><div class="anel anel2"></div><div class="anel anel3"></div></div>
<h1>CHAT CPA</h1>
<div class="dica-ia" id="dicaIA"></div>
<div class="credito">feito por samuca</div>
<script>
const dicas = [
    "<b>Voce sabia?</b> O CHAT CPA pode gerar imagens a partir de uma simples descricao em texto.",
    "<b>Dica:</b> No modo de voz, o CHAT CPA responde falando - otimo pra usar sem olhar pra tela.",
    "<b>Voce sabia?</b> O ZAP tem criptografia por frase - so quem sabe a senha le as mensagens.",
    "<b>Dica:</b> Toque e segure um video no Social CPA pra ele tocar com som.",
    "<b>Voce sabia?</b> O selo de verificado e o icone de cada app podem ser personalizados pelo dono.",
    "<b>Dica:</b> Voce pode instalar o CHAT CPA na tela inicial do celular, como um app de verdade.",
    "<b>Voce sabia?</b> O bot do CHAT CPA modera automaticamente o ZAP contra conteudo proibido.",
    "<b>Dica:</b> Cada conta tem um ID permanente - use ele pra adicionar contatos sem precisar do nome exato.",
];
const escolhida = dicas[Math.floor(Math.random() * dicas.length)];
document.getElementById("dicaIA").innerHTML = escolhida;
setTimeout(() => { window.location.href = "/inicio"; }, 2200);
</script>
</body></html>
"""

# ---------- LOGIN / CADASTRO (tela cheia: Google + email com codigo) ----------
PAGINA_LOGIN = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>CHAT CPA</title>
<style>
""" + ESTILO_COMUM + """
body { min-height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:24px; background:radial-gradient(circle at 50% 15%, #141414, #000000 70%); overflow-y:auto; }
.logo-img { width:88px; height:88px; border-radius:50%; margin-bottom:16px; box-shadow:0 0 24px #ffffff33; }
.cartao { width:100%; max-width:340px; display:flex; flex-direction:column; align-items:center; }
h2 { margin:0 0 22px; letter-spacing:1px; font-weight:300; font-size:22px; text-align:center; }
p.subtitulo { margin:-14px 0 20px; font-size:13px; color:#888; text-align:center; }
.etapa { width:100%; display:none; flex-direction:column; gap:12px; animation: aparecer 0.25s ease; }
.etapa.ativa { display:flex; }
@keyframes aparecer { from { opacity:0; transform:translateY(6px);} to { opacity:1; transform:translateY(0);} }
input, textarea { width:100%; padding:15px 16px; border-radius:12px; border:1px solid #ffffff22; background:#0a0a0a; color:#f2f2f2; font-size:15px; font-family:inherit; transition:border-color 0.15s ease; }
input:focus, textarea:focus { outline:none; border-color:#ffffff66; }
textarea { resize:vertical; min-height:60px; }
input.codigo { text-align:center; letter-spacing:10px; font-size:22px; font-weight:bold; padding-left:8px; }
label.campo-arquivo { display:flex; align-items:center; justify-content:center; gap:10px; padding:16px; border-radius:14px; border:1px dashed #ffffff33; color:#999; font-size:13px; cursor:pointer; transition:border-color 0.15s ease, color 0.15s ease; }
label.campo-arquivo:hover { border-color:#ffffff66; color:#ccc; }
.avatar-preview { width:64px; height:64px; border-radius:50%; object-fit:cover; border:1px solid #ffffff33; display:none; margin:0 auto; }
.linha-id-cadastro { display:flex; align-items:center; justify-content:center; gap:10px; padding:12px 16px; border-radius:12px; border:1px solid #ffffff22; background:#0a0a0a; }
.linha-id-cadastro span.rotulo { font-size:12px; color:#888; }
.linha-id-cadastro span.valor { font-size:18px; font-weight:bold; letter-spacing:1px; flex:1; text-align:center; }
.linha-id-cadastro button { background:none; border:none; color:#ffffff; text-decoration:underline; font-size:12px; cursor:pointer; padding:4px; }
button.principal { width:100%; padding:16px; border-radius:12px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; font-size:15px; margin-top:4px; transition:opacity 0.15s ease, transform 0.05s ease; }
button.principal:hover:not(:disabled) { opacity:0.88; }
button.principal:active:not(:disabled) { transform:scale(0.98); }
button.principal:disabled { opacity:0.45; cursor:default; }
button.link-sutil { background:none; border:none; color:#888; text-decoration:underline; font-size:12px; cursor:pointer; padding:6px; margin-top:2px; }
button.link-sutil:disabled { color:#444; text-decoration:none; cursor:default; }
.voltar-etapa { align-self:flex-start; color:#888; text-decoration:none; font-size:13px; cursor:pointer; margin-bottom:2px; }
.erro { color:#ff6666; font-size:13px; margin-top:14px; text-align:center; min-height:16px; }
.divisor { display:flex; align-items:center; gap:10px; color:#555; font-size:12px; margin:18px 0; width:100%; max-width:340px; }
.divisor::before, .divisor::after { content:""; flex:1; height:1px; background:#ffffff22; }
.bloco-google { display:flex; flex-direction:column; align-items:center; }
@media (max-width:420px) { h2 { font-size:19px; } input, textarea, button.principal { font-size:14px; padding:14px; } }
</style></head>
<body>
<img src="{logo_url}" class="logo-img" onerror="this.style.display='none'">
<h2>Entrar no CHAT CPA</h2>
<div class="cartao">
{bloco_google}

<!-- Etapa 1: email -->
<div class="etapa ativa" id="etapaEmail">
  <input type="email" id="campoEmail" placeholder="Seu email" autocomplete="email">
  <button type="button" class="principal" id="botaoEnviarCodigo" onclick="enviarCodigo()">Continuar</button>
</div>

<!-- Etapa 2: codigo recebido por email -->
<div class="etapa" id="etapaCodigo">
  <span class="voltar-etapa" onclick="irPara('etapaEmail')">&#8592; usar outro email</span>
  <p class="subtitulo" id="textoCodigoEnviado" style="margin-top:0;">Enviamos um codigo para o seu email</p>
  <input type="text" id="campoCodigo" class="codigo" maxlength="6" inputmode="numeric" placeholder="000000">
  <button type="button" class="principal" id="botaoConfirmarCodigo" onclick="confirmarCodigo()">Confirmar</button>
  <button type="button" class="link-sutil" id="botaoReenviar" onclick="enviarCodigo(true)">Reenviar codigo</button>
</div>

<!-- Etapa 3: cadastro (apenas para email novo) -->
<div class="etapa" id="etapaCadastro">
  <p class="subtitulo" style="margin-top:0;">Falta pouco! Complete seu perfil</p>
  <input type="text" id="campoApelido" placeholder="Apelido" maxlength="40">
  <img class="avatar-preview" id="previaAvatar">
  <label class="campo-arquivo" id="rotuloFoto">Foto de perfil
    <input type="file" id="inputFoto" accept="image/*" style="display:none">
  </label>
  <div class="linha-id-cadastro">
    <span class="rotulo">ID</span>
    <span class="valor" id="valorIdSugerido">#000000</span>
    <button type="button" onclick="sugerirNovoId()">gerar outro</button>
  </div>
  <input type="date" id="campoNascimento">
  <button type="button" class="principal" id="botaoFinalizar" onclick="finalizarCadastro()">Criar conta</button>
</div>

<div class="erro" id="mensagemErro"></div>
</div>
<script>
function aoLoginGoogle(resposta) {
    fetch("/auth/google", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({credential: resposta.credential})
    })
    .then(r => r.json())
    .then(dados => {
        if (dados.ok) { window.location.href = "/carregando"; }
        else { mostrarErro(dados.erro || "Nao foi possivel entrar com o Google."); }
    })
    .catch(() => mostrarErro("Falha ao conectar com o Google."));
}

let emailAtual = "";
let idEscolhido = 0;

function mostrarErro(texto) {
    document.getElementById("mensagemErro").textContent = texto || "";
}
function irPara(idEtapa) {
    document.querySelectorAll(".etapa").forEach(el => el.classList.remove("ativa"));
    document.getElementById(idEtapa).classList.add("ativa");
    mostrarErro("");
}
function definirCarregando(idBotao, carregando, textoNormal) {
    const botao = document.getElementById(idBotao);
    botao.disabled = carregando;
    botao.textContent = carregando ? "Enviando..." : textoNormal;
}

async function enviarCodigo(reenvio) {
    const email = document.getElementById("campoEmail").value.trim();
    if (!reenvio) {
        if (!email) { mostrarErro("Digite um email valido."); return; }
        emailAtual = email;
    }
    mostrarErro("");
    definirCarregando(reenvio ? "botaoReenviar" : "botaoEnviarCodigo", true, reenvio ? "Reenviar codigo" : "Continuar");
    try {
        const r = await fetch("/auth/enviar_codigo", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({email: emailAtual})
        });
        const dados = await r.json();
        if (dados.ok) {
            if (dados.codigo_teste) {
                document.getElementById("textoCodigoEnviado").textContent = "Envio de email nao esta configurado no servidor. Codigo de teste: " + dados.codigo_teste;
                document.getElementById("campoCodigo").value = dados.codigo_teste;
            } else {
                document.getElementById("textoCodigoEnviado").textContent = "Enviamos um codigo para " + emailAtual;
                document.getElementById("campoCodigo").value = "";
            }
            irPara("etapaCodigo");
        } else {
            mostrarErro(dados.erro || "Nao foi possivel enviar o codigo.");
        }
    } catch (e) {
        mostrarErro("Falha de conexao. Tente novamente.");
    }
    definirCarregando(reenvio ? "botaoReenviar" : "botaoEnviarCodigo", false, reenvio ? "Reenviar codigo" : "Continuar");
}

async function confirmarCodigo() {
    const codigo = document.getElementById("campoCodigo").value.trim();
    if (codigo.length !== 6) { mostrarErro("Digite o codigo de 6 digitos."); return; }
    mostrarErro("");
    definirCarregando("botaoConfirmarCodigo", true, "Confirmar");
    try {
        const r = await fetch("/auth/verificar_codigo", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({email: emailAtual, codigo: codigo})
        });
        const dados = await r.json();
        if (!dados.ok) {
            mostrarErro(dados.erro || "Codigo incorreto.");
        } else if (dados.precisa_cadastro) {
            idEscolhido = dados.id_sugerido;
            document.getElementById("valorIdSugerido").textContent = "#" + dados.id_sugerido;
            irPara("etapaCadastro");
        } else {
            window.location.href = "/carregando";
        }
    } catch (e) {
        mostrarErro("Falha de conexao. Tente novamente.");
    }
    definirCarregando("botaoConfirmarCodigo", false, "Confirmar");
}

async function sugerirNovoId() {
    const r = await fetch("/auth/sugerir_id");
    const dados = await r.json();
    idEscolhido = dados.id_publico;
    document.getElementById("valorIdSugerido").textContent = "#" + dados.id_publico;
}

document.getElementById("inputFoto").addEventListener("change", function() {
    const arquivo = this.files[0];
    const previa = document.getElementById("previaAvatar");
    document.getElementById("rotuloFoto").firstChild.textContent = arquivo ? arquivo.name : "Foto de perfil ";
    if (arquivo) {
        previa.src = URL.createObjectURL(arquivo);
        previa.style.display = "block";
    }
});

async function finalizarCadastro() {
    const apelido = document.getElementById("campoApelido").value.trim();
    if (!apelido) { mostrarErro("Escolha um apelido."); return; }
    mostrarErro("");
    definirCarregando("botaoFinalizar", true, "Criar conta");
    const form = new FormData();
    form.append("apelido", apelido);
    form.append("id_publico", idEscolhido);
    form.append("data_nascimento", document.getElementById("campoNascimento").value);
    const arquivoFoto = document.getElementById("inputFoto").files[0];
    if (arquivoFoto) form.append("foto_perfil", arquivoFoto);
    try {
        const r = await fetch("/auth/completar_cadastro", { method: "POST", body: form });
        const dados = await r.json();
        if (dados.ok) { window.location.href = "/carregando"; }
        else { mostrarErro(dados.erro || "Nao foi possivel criar a conta."); }
    } catch (e) {
        mostrarErro("Falha de conexao. Tente novamente.");
    }
    definirCarregando("botaoFinalizar", false, "Criar conta");
}
</script>
</body></html>
"""

# ---------- INICIO (estilo tela de celular) ----------
PAGINA_INICIO = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>CHAT CPA</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#000000">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="apple-touch-icon" href="{icone_app_url}">
<style>
""" + ESTILO_COMUM + """
body {
  height:100vh; display:flex; flex-direction:column;
  background-image: linear-gradient(180deg, #000000cc, #000000ee), url('{fundo_url}');
  background-size: cover; background-position: center; background-attachment: fixed;
}
.status-topo { text-align:center; padding:30px 0 6px; }
.marca-topo { font-size:13px; font-weight:bold; letter-spacing:2px; color:#3ddc6a; text-shadow:0 0 10px #3ddc6a55; }
.marca-topo span { display:block; font-size:10px; font-weight:400; letter-spacing:1px; color:#888; margin-top:2px; text-shadow:none; }
.relogio { font-size:44px; font-weight:200; letter-spacing:2px; }
.data { font-size:13px; color:#888; margin-top:4px; }
.contadores { display:flex; align-items:center; justify-content:center; gap:18px; margin-top:12px; font-size:12px; color:#ccc; }
.contador-item { display:flex; align-items:center; gap:6px; background:#0d0d0dcc; border:1px solid #ffffff22; padding:6px 12px; border-radius:20px; }
.pontinho-online { width:8px; height:8px; border-radius:50%; background:#3ddc6a; box-shadow:0 0 6px #3ddc6a; }
.apps { flex:1; display:flex; align-items:center; justify-content:center; gap:30px; flex-wrap:wrap; padding:20px; }
.app-icone { display:flex; flex-direction:column; align-items:center; gap:10px; cursor:pointer; text-decoration:none; color:#f2f2f2; }
.icone-quadrado { width:64px; height:64px; border-radius:18px; background:#0d0d0dcc; border:1px solid #ffffff22; display:flex; align-items:center; justify-content:center; font-size:26px; backdrop-filter: blur(2px); overflow:hidden; }
.icone-quadrado img { width:100%; height:100%; object-fit:cover; }
.rodape { text-align:center; padding:16px; font-size:12px; color:#666; }
.sair-link { color:#888; text-decoration:underline; cursor:pointer; }
@media (max-width:480px) { .relogio { font-size:36px; } .apps { gap:22px; } }
</style></head>
<body>
<div class="status-topo">
  <div class="marca-topo">CHAT CPA<span>CRIPTOGRAFADO PARA AJUDAR</span></div>
  <div class="relogio" id="relogio">--:--</div>
  <div class="data" id="dataAtual"></div>
  <div class="contadores">
    <div class="contador-item"><span class="pontinho-online"></span><span id="qtdOnline">{qtd_online}</span> online</div>
    <div class="contador-item"><span id="qtdContas">{qtd_contas}</span> contas</div>
  </div>
</div>
<div class="apps">
  <a class="app-icone" href="/rede"><div class="icone-quadrado">{icone_jarvisweb}</div>Social CPA</a>
  <a class="app-icone" href="/painel"><div class="icone-quadrado">{icone_jarvis}</div>CHAT CPA</a>
  <a class="app-icone" href="/zap"><div class="icone-quadrado">{icone_zap}</div>ZAP</a>
  <a class="app-icone" href="/extensao"><div class="icone-quadrado">&lt;/&gt;</div>CPA Codes</a>
  <a class="app-icone" href="/suporte"><div class="icone-quadrado">{icone_suporte}</div>Suporte</a>
  <a class="app-icone" href="/baixar"><div class="icone-quadrado">&#8595;</div>Baixar app</a>
</div>
<div class="rodape"><span class="sair-link" onclick="location.href='/logout'">Sair da conta</span></div>
<script>
function atualizarRelogio() {
    const agora = new Date();
    const h = String(agora.getHours()).padStart(2,'0');
    const m = String(agora.getMinutes()).padStart(2,'0');
    document.getElementById("relogio").textContent = h + ":" + m;
    const dias = ["domingo","segunda-feira","terca-feira","quarta-feira","quinta-feira","sexta-feira","sabado"];
    document.getElementById("dataAtual").textContent = dias[agora.getDay()] + ", " + agora.getDate() + "/" + (agora.getMonth()+1);
}
atualizarRelogio();
setInterval(atualizarRelogio, 1000);

// avisa o servidor que esta conta esta online e atualiza os contadores
async function pulsarPresenca() {
    try {
        const r = await fetch("/heartbeat", { method: "POST" });
        const d = await r.json();
        if (d && d.ok) {
            document.getElementById("qtdOnline").textContent = d.online;
            document.getElementById("qtdContas").textContent = d.contas;
        }
    } catch (e) {}
}
pulsarPresenca();
setInterval(pulsarPresenca, 20000);
</script>
</body></html>
"""

# ---------- CHAT (painel) ----------
PAGINA = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>CHAT CPA</title>
<style>
""" + ESTILO_COMUM + """
body { display:flex; height:100vh; overflow:hidden; }
.sidebar { width:260px; background:#0d0d0d; border-right:1px solid #ffffff22; padding:16px; display:flex; flex-direction:column; transition: margin-left 0.2s ease; flex-shrink:0; }
.sidebar.recolhida { margin-left:-260px; }
.logo-linha { display:flex; align-items:center; gap:10px; margin-bottom:20px; }
.menu-icone { display:flex; flex-direction:column; gap:4px; padding:6px; cursor:pointer; }
.menu-icone span { width:20px; height:2px; background:#ffffff; display:block; }
.logo-link { display:flex; align-items:center; gap:10px; text-decoration:none; color:#f2f2f2; cursor:pointer; }
.logo-img { width:36px; height:36px; border-radius:50%; object-fit:cover; box-shadow:0 0 12px #ffffff55; }
.novo-chat { background:#1a1a1a; color:#f2f2f2; border:1px solid #ffffff33; border-radius:8px; padding:10px; text-align:left; cursor:pointer; margin-bottom:10px; }
.link-rede, .link-inicio, .link-suporte { background:#1a1a1a; color:#f2f2f2; border:1px solid #ffffff33; border-radius:8px; padding:10px; text-align:left; cursor:pointer; margin-bottom:8px; display:block; text-decoration:none; }
.historico-lista { flex:1; overflow-y:auto; font-size:13px; margin-top:8px; }
.item-hist { padding:8px; border-radius:6px; margin-bottom:4px; cursor:pointer; color:#cccccc; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.item-hist:hover, .item-hist.ativo { background:#1a1a1a; }
.rodape-sidebar { font-size:12px; color:#aaaaaa; display:flex; justify-content:space-between; align-items:center; margin-top:10px; gap:6px; }
.sair { cursor:pointer; text-decoration:underline; }
.principal { flex:1; display:flex; flex-direction:column; min-width:0; }
.topo { padding:16px; border-bottom:1px solid #ffffff22; font-weight:bold; letter-spacing:1px; display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
.topo-esquerda { display:flex; align-items:center; gap:10px; }
.menu-icone-mobile { display:none; cursor:pointer; }
.seta-inicio { color:#ffffff; text-decoration:none; font-size:20px; }
.seletor-voz { background:#0d0d0d; color:#f2f2f2; border:1px solid #ffffff33; border-radius:6px; padding:6px; font-size:12px; }
.mensagens { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:16px; }
.msg { max-width:70%; padding:12px 16px; border-radius:12px; line-height:1.4; }
.msg.usuario { align-self:flex-end; background:#1a1a1a; color:#f2f2f2; }
.msg.jarvis { align-self:flex-start; background:#0d0d0d; border:1px solid #ffffff22; color:#f2f2f2; }
.msg img { max-width:100%; border-radius:8px; margin-top:8px; }
.botao-falar-msg { background:none; border:none; color:#ffffff; cursor:pointer; font-size:14px; margin-top:6px; text-decoration:underline; }
.spinner { width:24px; height:24px; border-radius:50%; border:3px solid #1a1a1a; border-top-color:#ffffff; animation: girar 0.8s linear infinite; flex-shrink:0; }
@keyframes girar { to { transform: rotate(360deg); } }
.pontos-carregando { display:flex; gap:4px; padding:4px 0; }
.pontos-carregando span { width:6px; height:6px; border-radius:50%; background:#ffffff; animation: pulsar 1s infinite ease-in-out; }
.pontos-carregando span:nth-child(2) { animation-delay: 0.15s; }
.pontos-carregando span:nth-child(3) { animation-delay: 0.3s; }
@keyframes pulsar { 0%, 80%, 100% { opacity:0.2; transform:scale(0.8);} 40% { opacity:1; transform:scale(1.2);} }
.area-input { padding:16px 16px calc(16px + env(safe-area-inset-bottom)); border-top:1px solid #ffffff22; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.area-input input[type=text] { flex:1; min-width:120px; padding:14px; border-radius:8px; border:1px solid #ffffff33; background:#0d0d0d; color:#f2f2f2; font-size:14px; }
.area-input button { padding:14px 16px; border-radius:8px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; font-size:13px; }
.area-input button.secundario { background:#1a1a1a; color:#f2f2f2; border:1px solid #ffffff33; }
.botao-icone { background:#1a1a1a !important; color:#f2f2f2 !important; border:1px solid #ffffff33 !important; padding:12px 14px !important; display:flex; align-items:center; }
.botao-icone.gravando { background:#ff3b3b !important; color:#fff !important; }
.modal-fundo { display:none; position:fixed; inset:0; background:#000c; align-items:center; justify-content:center; z-index:10; }
.modal-fundo.aberto { display:flex; }
.modal-caixa { background:#0d0d0d; border:1px solid #ffffff33; border-radius:12px; padding:24px; width:320px; max-width:90vw; }
.modal-caixa h3 { margin-top:0; }
.modal-caixa input, .modal-caixa select { width:100%; padding:10px; margin-top:8px; border-radius:6px; border:1px solid #ffffff33; background:#000000; color:#f2f2f2; }
.modal-botoes { display:flex; gap:8px; margin-top:16px; }
.modal-botoes button { flex:1; padding:10px; border-radius:6px; border:none; cursor:pointer; font-weight:bold; }
.modal-botoes .confirmar { background:#ffffff; color:#000000; }
.modal-botoes .cancelar { background:#1a1a1a; color:#f2f2f2; }
.avatar-pequeno { width:28px; height:28px; border-radius:50%; object-fit:cover; cursor:pointer; }

.overlay-voz { display:none; position:fixed; inset:0; background:#000000; z-index:50; flex-direction:column; align-items:center; justify-content:center; }
.overlay-voz.aberto { display:flex; }
.fechar-voz { position:absolute; top:20px; right:20px; width:40px; height:40px; border-radius:50%; background:#1a1a1a; color:#fff; border:1px solid #ffffff33; font-size:20px; cursor:pointer; z-index:2; }
.circulo-voz { width:170px; height:170px; border-radius:50%; border:2px solid #ffffff55; display:flex; align-items:center; justify-content:center; position:relative; }
.circulo-voz .nucleo { width:70px; height:70px; border-radius:50%; background:#111; border:1px solid #fff; transition: transform 0.15s ease; }
.circulo-voz.falando .nucleo { transform: scale(1.25); background:#fff2; }
.circulo-voz.ouvindo .nucleo { transform: scale(1.1); }
.mascote-img { width:150px; height:150px; object-fit:contain; position:absolute; transition:transform 0.15s ease; z-index:2; }
.circulo-voz.falando .mascote-img { transform:scale(1.15) rotate(-1deg); }
.circulo-voz.ouvindo .mascote-img { transform:scale(1.05); }
.circulo-voz:not(.sem-mascote) .nucleo { display:none; }
.anel-voz { position:absolute; border-radius:50%; border:1px solid #ffffff33; }
.anel-voz.a1 { inset:-20px; animation: pulsoVoz 2s infinite; }
.anel-voz.a2 { inset:-40px; animation: pulsoVoz 2s infinite 0.4s; }
@keyframes pulsoVoz { 0% { opacity:0.6; transform:scale(0.9);} 100% { opacity:0; transform:scale(1.3);} }
.texto-voz { margin-top:30px; color:#ccc; text-align:center; max-width:80%; min-height:24px; }
.estado-voz { margin-top:10px; color:#666; font-size:13px; }

@media (max-width: 720px) {
  .sidebar { position:fixed; z-index:20; height:100vh; }
  .sidebar:not(.recolhida) { margin-left:0; }
  .sidebar.recolhida { margin-left:-260px; }
  .menu-icone-mobile { display:flex; }
  .msg { max-width:88%; }
  .area-input button { padding:12px; font-size:12px; }
  .circulo-voz { width:130px; height:130px; }
  .mascote-img { width:110px; height:110px; }
}
</style></head>
<body>
<div class="sidebar" id="sidebar">
  <div class="logo-linha">
    <div class="menu-icone" onclick="alternarSidebar()"><span></span><span></span><span></span></div>
    <a class="logo-link" href="/inicio">
      <img src="/static/logo.jpg" class="logo-img" onerror="this.style.display='none'">
      <strong>CHAT CPA</strong>
    </a>
  </div>
  <a class="link-inicio" href="/inicio">Tela inicial</a>
  <button class="novo-chat" onclick="novaConversa()">+ Nova conversa</button>
  <a class="link-suporte" href="/suporte">Suporte</a>
  <div class="historico-lista" id="listaConversas"></div>
  <div class="rodape-sidebar">
    <img class="avatar-pequeno" src="{avatar_url}" onclick="location.href='/perfil/{usuario}'">
    <span style="flex:1;">{usuario} {selo_tag}</span>
    <span class="sair" onclick="location.href='/logout'">Sair</span>
  </div>
</div>
<div class="principal">
  <div class="topo">
    <div class="topo-esquerda">
      <a href="/inicio" class="seta-inicio" title="Tela inicial">&#8592;</a>
      <div class="menu-icone menu-icone-mobile" onclick="alternarSidebar()"><span></span><span></span><span></span></div>
      <span>CHAT CPA</span>
    </div>
    <select class="seletor-voz" id="seletorVoz"></select>
  </div>
  <div class="mensagens" id="mensagens"></div>
  <div class="area-input">
    <button class="botao-icone" id="botaoMic" onclick="alternarMicrofone()" title="Falar um comando">""" + ICONE_MIC + """</button>
    <button class="botao-icone" onclick="abrirModoVoz()" title="Conversar so por voz">""" + ICONE_ONDA + """</button>
    <input type="text" id="campo" placeholder="Pergunte qualquer coisa...">
    <button class="secundario" onclick="gerarImagem()">Gerar imagem</button>
    <button class="secundario" onclick="abrirModalConverter()">Converter</button>
    <button onclick="enviarTexto()">Enviar</button>
  </div>
</div>

<div class="modal-fundo" id="modalConverter">
  <div class="modal-caixa">
    <h3>Converter / redimensionar imagem</h3>
    <input type="file" id="arquivoImagem" accept="image/*">
    <input type="number" id="larguraNova" placeholder="Largura (px)">
    <input type="number" id="alturaNova" placeholder="Altura (px)">
    <select id="formatoNovo"><option value="png">PNG</option><option value="gif">GIF</option><option value="jpeg">JPEG</option></select>
    <div class="modal-botoes">
      <button class="cancelar" onclick="fecharModalConverter()">Cancelar</button>
      <button class="confirmar" onclick="converterImagem()">Converter</button>
    </div>
  </div>
</div>

<div class="overlay-voz" id="overlayVoz">
  <div class="fechar-voz" onclick="fecharModoVoz()" title="Parar de falar">&times;</div>
  <div class="circulo-voz" id="circuloVoz">
    <img id="mascoteJarvis" class="mascote-img" src="/static/mascote.png" onerror="this.style.display='none'; document.getElementById('circuloVoz').classList.add('sem-mascote');">
    <div class="anel-voz a1"></div><div class="anel-voz a2"></div>
    <div class="nucleo"></div>
  </div>
  <div class="texto-voz" id="textoVozAtual">Toque no X para sair. Fale quando quiser.</div>
  <div class="estado-voz" id="estadoVoz">Ouvindo...</div>
</div>

<script>
const ICONE_MIC_HTML = `""" + ICONE_MIC + """`;
const ICONE_MIC_OFF_HTML = `""" + ICONE_MIC_OFF + """`;

function alternarSidebar() { document.getElementById("sidebar").classList.toggle("recolhida"); }

let vozes = [];
function carregarVozes() {
    vozes = speechSynthesis.getVoices().filter(v => v.lang.startsWith("pt"));
    const seletor = document.getElementById("seletorVoz");
    seletor.innerHTML = "";
    if (vozes.length === 0) { seletor.innerHTML = "<option>Nenhuma voz PT</option>"; return; }
    vozes.forEach((v, i) => {
        const opcao = document.createElement("option");
        opcao.value = i; opcao.textContent = v.name;
        seletor.appendChild(opcao);
    });
}
speechSynthesis.onvoiceschanged = carregarVozes;
carregarVozes();

function falarTexto(texto, aoTerminar) {
    const seletor = document.getElementById("seletorVoz");
    const utter = new SpeechSynthesisUtterance(texto);
    if (vozes[seletor.value]) utter.voice = vozes[seletor.value];
    utter.lang = "pt-BR";
    if (aoTerminar) utter.onend = aoTerminar;
    speechSynthesis.speak(utter);
}

let conversas = JSON.parse(localStorage.getItem("jarvis_conversas_completas") || "[]");
let indiceAtual = -1;
function salvarConversas() { localStorage.setItem("jarvis_conversas_completas", JSON.stringify(conversas)); }
function renderizarSidebar() {
    const container = document.getElementById("listaConversas");
    container.innerHTML = "";
    conversas.forEach((c, i) => {
        const item = document.createElement("div");
        item.className = "item-hist" + (i === indiceAtual ? " ativo" : "");
        const titulo = c.titulo || "Conversa";
        item.textContent = titulo.length > 10 ? titulo.slice(0, 10) + "..." : titulo;
        item.onclick = (ev) => { ev.stopPropagation(); abrirConversa(i); };
        container.appendChild(item);
    });
}
function renderizarMensagens() {
    const div = document.getElementById("mensagens");
    div.innerHTML = "";
    if (indiceAtual === -1) return;
    conversas[indiceAtual].mensagens.forEach(m => adicionarMensagemDOM(m.remetente, m.html, m.audio));
    div.scrollTop = div.scrollHeight;
}
function abrirConversa(indice) {
    indiceAtual = indice;
    renderizarSidebar(); renderizarMensagens();
    if (window.innerWidth <= 720) document.getElementById("sidebar").classList.add("recolhida");
}
function novaConversa() {
    conversas.unshift({titulo: "", mensagens: []});
    indiceAtual = 0;
    salvarConversas(); renderizarSidebar(); renderizarMensagens();
}
function adicionarMensagemDOM(remetente, conteudoHtml, comAudio) {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg " + remetente;
    bolha.innerHTML = conteudoHtml;
    if (comAudio) {
        const botaoAudio = document.createElement("button");
        botaoAudio.className = "botao-falar-msg";
        botaoAudio.textContent = "Ouvir";
        botaoAudio.onclick = () => falarTexto(comAudio);
        bolha.appendChild(botaoAudio);
    }
    div.appendChild(bolha);
    div.scrollTop = div.scrollHeight;
    return bolha;
}
function adicionarMensagem(remetente, conteudoHtml, comAudio) {
    if (indiceAtual === -1) novaConversa();
    const bolha = adicionarMensagemDOM(remetente, conteudoHtml, comAudio);
    conversas[indiceAtual].mensagens.push({remetente, html: conteudoHtml, audio: comAudio || null});
    if (!conversas[indiceAtual].titulo) { conversas[indiceAtual].titulo = bolha.textContent.trim(); renderizarSidebar(); }
    salvarConversas();
    return bolha;
}
function adicionarCarregandoTexto() {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg jarvis";
    bolha.innerHTML = '<div class="pontos-carregando"><span></span><span></span><span></span></div>';
    div.appendChild(bolha); div.scrollTop = div.scrollHeight;
    return bolha;
}
function adicionarCarregandoImagem(texto) {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg jarvis";
    bolha.innerHTML = '<div style="display:flex;align-items:center;gap:10px;"><div class="spinner"></div><span>' + texto + '</span></div>';
    div.appendChild(bolha); div.scrollTop = div.scrollHeight;
    return bolha;
}

async function pedirResposta(mensagem) {
    try {
        const resposta = await fetch("/chat", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mensagem}) });
        const dados = await resposta.json();
        return dados.resposta || "Nao consegui responder agora, tenta de novo.";
    } catch (erro) {
        return "Falha de conexao. Verifica sua internet e tenta de novo.";
    }
}

async function enviarTexto() {
    const campo = document.getElementById("campo");
    const texto = campo.value.trim();
    if (!texto) return;
    adicionarMensagem("usuario", texto);
    campo.value = "";
    const carregando = adicionarCarregandoTexto();
    const respostaTexto = await pedirResposta(texto);
    carregando.remove();
    adicionarMensagem("jarvis", respostaTexto, respostaTexto);
}

async function gerarImagem() {
    const campo = document.getElementById("campo");
    const prompt = campo.value.trim();
    if (!prompt) return;
    adicionarMensagem("usuario", "Gerar imagem: " + prompt);
    campo.value = "";
    const carregando = adicionarCarregandoImagem("Criando imagem...");
    let dados;
    try {
        const resposta = await fetch("/imagem?prompt=" + encodeURIComponent(prompt));
        dados = await resposta.json();
    } catch (erro) {
        carregando.remove();
        adicionarMensagem("jarvis", "Falha de conexao ao gerar a imagem. Tenta de novo.");
        return;
    }
    carregando.remove();
    if (!dados.url) { adicionarMensagem("jarvis", dados.erro || "Nao consegui gerar a imagem, tenta de novo."); return; }
    const img = new Image();
    img.onload = () => adicionarMensagem("jarvis", "Aqui esta:<br><img src='" + dados.url + "'>");
    img.onerror = () => adicionarMensagem("jarvis", "Nao consegui gerar a imagem, tenta de novo.");
    img.src = dados.url;
}

document.getElementById("campo").addEventListener("keydown", function(e) { if (e.key === "Enter") enviarTexto(); });
function abrirModalConverter() { document.getElementById("modalConverter").classList.add("aberto"); }
function fecharModalConverter() { document.getElementById("modalConverter").classList.remove("aberto"); }
async function converterImagem() {
    const arquivo = document.getElementById("arquivoImagem").files[0];
    if (!arquivo) { alert("Escolha um arquivo primeiro."); return; }
    const largura = document.getElementById("larguraNova").value;
    const altura = document.getElementById("alturaNova").value;
    const formato = document.getElementById("formatoNovo").value;
    const dadosForm = new FormData();
    dadosForm.append("arquivo", arquivo); dadosForm.append("largura", largura);
    dadosForm.append("altura", altura); dadosForm.append("formato", formato);
    fecharModalConverter();
    adicionarMensagem("usuario", "Converter imagem: " + arquivo.name);
    const carregando = adicionarCarregandoImagem("Convertendo...");
    const resposta = await fetch("/converter", { method: "POST", body: dadosForm });
    carregando.remove();
    if (!resposta.ok) { adicionarMensagem("jarvis", "Nao consegui converter essa imagem."); return; }
    const blob = await resposta.blob();
    const url = URL.createObjectURL(blob);
    adicionarMensagem("jarvis", "Pronto:<br><img src='" + url + "'><br><a href='" + url + "' download='convertida." + formato + "' style='color:#fff;'>Baixar</a>");
}

// ---- Comando por voz unico ----
let reconhecimento = null;
let gravando = false;
function alternarMicrofone() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) { alert("Seu navegador nao suporta reconhecimento de voz. Tente no Chrome."); return; }
    const botao = document.getElementById("botaoMic");
    if (gravando) { reconhecimento.stop(); return; }
    pausarPalavraChave();
    reconhecimento = new SpeechRecognition();
    reconhecimento.lang = "pt-BR";
    reconhecimento.interimResults = false;
    reconhecimento.onstart = () => { gravando = true; botao.classList.add("gravando"); botao.innerHTML = ICONE_MIC_OFF_HTML; };
    reconhecimento.onend = () => { gravando = false; botao.classList.remove("gravando"); botao.innerHTML = ICONE_MIC_HTML; retomarPalavraChave(); };
    reconhecimento.onresult = (evento) => { document.getElementById("campo").value = evento.results[0][0].transcript; enviarTexto(); };
    reconhecimento.start();
}

// ---- Modo de voz continuo (tela cheia) ----
let modoVozAtivo = false;
let reconhecimentoVoz = null;

function abrirModoVoz() {
    pausarPalavraChave();
    document.getElementById("overlayVoz").classList.add("aberto");
    document.getElementById("textoVozAtual").textContent = "Pode falar...";
    iniciarEscutaContinua();
}
function fecharModoVoz() {
    modoVozAtivo = false;
    if (reconhecimentoVoz) { try { reconhecimentoVoz.onend = null; reconhecimentoVoz.stop(); } catch(e){} }
    speechSynthesis.cancel();
    document.getElementById("overlayVoz").classList.remove("aberto");
    document.getElementById("circuloVoz").classList.remove("falando", "ouvindo");
    retomarPalavraChave();
}
function iniciarEscutaContinua() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) { alert("Seu navegador nao suporta reconhecimento de voz. Tente no Chrome."); fecharModoVoz(); return; }
    modoVozAtivo = true;
    reconhecimentoVoz = new SpeechRecognition();
    reconhecimentoVoz.lang = "pt-BR";
    reconhecimentoVoz.interimResults = false;

    reconhecimentoVoz.onstart = () => {
        document.getElementById("circuloVoz").classList.add("ouvindo");
        document.getElementById("estadoVoz").textContent = "Ouvindo...";
    };
    reconhecimentoVoz.onresult = async (evento) => {
        const texto = evento.results[0][0].transcript;
        document.getElementById("circuloVoz").classList.remove("ouvindo");
        document.getElementById("textoVozAtual").textContent = texto;
        document.getElementById("estadoVoz").textContent = "Pensando...";
        adicionarMensagem("usuario", texto);
        const respostaTexto = await pedirResposta(texto);
        adicionarMensagem("jarvis", respostaTexto, respostaTexto);
        if (!modoVozAtivo) return;
        document.getElementById("textoVozAtual").textContent = respostaTexto;
        document.getElementById("estadoVoz").textContent = "Falando...";
        document.getElementById("circuloVoz").classList.add("falando");
        falarTexto(respostaTexto, () => {
            document.getElementById("circuloVoz").classList.remove("falando");
            if (modoVozAtivo) iniciarEscutaContinua();
        });
    };
    reconhecimentoVoz.onerror = () => { if (modoVozAtivo) setTimeout(() => { if (modoVozAtivo) iniciarEscutaContinua(); }, 600); };
    reconhecimentoVoz.onend = () => { document.getElementById("circuloVoz").classList.remove("ouvindo"); };
    reconhecimentoVoz.start();
}

// ---- Palavra-chave "jarvis" para reabrir o menu quando a sidebar estiver fechada ----
let escutaPalavraChave = null;
let palavraChaveAtiva = false;
function iniciarPalavraChave() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return;
    try {
        escutaPalavraChave = new SpeechRecognition();
        escutaPalavraChave.lang = "pt-BR";
        escutaPalavraChave.continuous = true;
        escutaPalavraChave.interimResults = true;
        escutaPalavraChave.onresult = (evento) => {
            const ultimo = evento.results[evento.results.length - 1];
            const texto = (ultimo[0].transcript || "").toLowerCase();
            if (texto.includes("jarvis")) {
                document.getElementById("sidebar").classList.remove("recolhida");
            }
        };
        escutaPalavraChave.onend = () => { if (palavraChaveAtiva) setTimeout(iniciarPalavraChave, 400); };
        escutaPalavraChave.onerror = () => { if (palavraChaveAtiva) setTimeout(iniciarPalavraChave, 1200); };
        escutaPalavraChave.start();
    } catch (e) {}
}
function pausarPalavraChave() {
    palavraChaveAtiva = false;
    if (escutaPalavraChave) { try { escutaPalavraChave.onend = null; escutaPalavraChave.stop(); } catch(e){} }
}
function retomarPalavraChave() {
    palavraChaveAtiva = true;
    setTimeout(iniciarPalavraChave, 800);
}
palavraChaveAtiva = true;
iniciarPalavraChave();

if (conversas.length > 0) { indiceAtual = 0; }
renderizarSidebar();
renderizarMensagens();
</script>
</body></html>
"""

# ---------- REDE (Social CPA) ----------
PAGINA_REDE = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Social CPA</title>
<style>
""" + ESTILO_COMUM + """
html, body { height:100%; overflow:hidden; background:#000; }
.topo { position:fixed; top:0; left:0; right:0; background:linear-gradient(#000000cc, transparent); padding:14px 16px; display:flex; align-items:center; gap:12px; z-index:15; pointer-events:none; }
.topo > * { pointer-events:auto; }
.voltar { color:#ffffff; text-decoration:none; font-size:20px; text-shadow:0 1px 4px #000; }
.titulo-topo { font-weight:bold; letter-spacing:1px; text-shadow:0 1px 4px #000; }
.botao-engrenagem { margin-left:auto; background:#00000066; border:1px solid #ffffff33; color:#fff; width:34px; height:34px; border-radius:50%; display:flex; align-items:center; justify-content:center; cursor:pointer; font-size:16px; }
.container { height:100%; }
.feed-tela-cheia { height:100%; overflow-y:scroll; scroll-snap-type:y mandatory; scrollbar-width:none; }
.feed-tela-cheia::-webkit-scrollbar { display:none; }
.feed-vazio { height:100%; display:flex; align-items:center; justify-content:center; color:#777; font-size:14px; text-align:center; padding:0 30px; }
.caixa-postar { background:#0d0d0d; border:1px solid #ffffff22; border-radius:12px; padding:14px; margin-bottom:20px; }
.caixa-postar textarea { width:100%; background:#000000; border:1px solid #ffffff22; border-radius:8px; color:#f2f2f2; padding:10px; resize:vertical; min-height:60px; }
.caixa-postar input { width:100%; margin-top:8px; padding:8px; border-radius:6px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; font-size:12px; }
.caixa-postar button { margin-top:10px; padding:10px 18px; border-radius:8px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; }
.modal-postar { display:none; position:fixed; inset:0; background:#000000dd; z-index:110; align-items:flex-end; justify-content:center; }
.modal-postar.aberto { display:flex; }
.modal-postar-conteudo { width:100%; max-width:480px; background:#0d0d0d; border-radius:16px 16px 0 0; padding:16px; border:1px solid #ffffff22; border-bottom:none; }
.modal-postar-topo { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
.modal-postar-topo b { font-size:15px; }
.modal-postar-topo span { cursor:pointer; color:#888; font-size:22px; line-height:1; }
.nav-inferior { position:fixed; bottom:0; left:0; right:0; display:flex; justify-content:space-around; align-items:center; background:#0d0d0dee; backdrop-filter:blur(6px); border-top:1px solid #ffffff22; padding:10px 0 calc(10px + env(safe-area-inset-bottom)); z-index:50; }
.nav-inferior a, .nav-inferior div.nav-item { display:flex; flex-direction:column; align-items:center; gap:2px; color:#ccc; text-decoration:none; font-size:10px; cursor:pointer; }
.nav-inferior .nav-mais { width:42px; height:32px; border-radius:10px; background:#ffffff; color:#000; display:flex; align-items:center; justify-content:center; font-size:20px; font-weight:bold; }
.nav-inferior img.nav-icone { width:22px; height:22px; border-radius:6px; object-fit:cover; }
.post { height:100%; width:100%; scroll-snap-align:start; position:relative; display:flex; align-items:center; justify-content:center; background:#000; overflow:hidden; }
.post-midia-wrap { width:100%; height:100%; display:flex; align-items:center; justify-content:center; background:#000; }
.post img.post-imagem, .post video { max-width:100%; max-height:100%; width:auto; height:auto; object-fit:contain; }
.post-sem-midia { padding:24px; font-size:20px; text-align:center; color:#f2f2f2; white-space:pre-wrap; }
.post-rodape { position:absolute; left:0; right:78px; bottom:0; padding:16px 14px calc(70px + env(safe-area-inset-bottom)); background:linear-gradient(transparent, #000000cc 70%); }
.post-cabecalho { display:flex; align-items:center; gap:8px; margin-bottom:6px; font-weight:bold; }
.post-cabecalho a { color:#f2f2f2; text-decoration:none; display:flex; align-items:center; gap:8px; }
.post-cabecalho img { width:34px; height:34px; border-radius:50%; object-fit:cover; border:1px solid #ffffff44; }
.post-texto { margin:4px 0 0; white-space:pre-wrap; font-size:13px; color:#eee; }
.acoes-laterais { position:absolute; right:10px; bottom:158px; display:flex; flex-direction:column; align-items:center; gap:20px; z-index:8; }
.acao-lateral { display:flex; flex-direction:column; align-items:center; gap:3px; cursor:pointer; color:#fff; font-size:11px; text-shadow:0 1px 3px #000; }
.acao-lateral svg { width:30px; height:30px; filter:drop-shadow(0 1px 3px #000); }
.acao-lateral.curtido svg path { fill:#ff3b5c; stroke:#ff3b5c; }
.acao-lateral.salvo svg path { fill:#ffffff; }
.botao-seguir-lateral { margin-top:2px; background:#ff3b5c; color:#fff; border:none; border-radius:6px; padding:3px 8px; font-size:10px; font-weight:bold; cursor:pointer; }
.folha-comentarios { display:none; position:fixed; inset:0; background:#00000099; z-index:120; align-items:flex-end; justify-content:center; }
.folha-comentarios.aberta { display:flex; }
.folha-comentarios-conteudo { width:100%; max-width:480px; max-height:70vh; background:#0d0d0d; border-radius:16px 16px 0 0; border:1px solid #ffffff22; border-bottom:none; display:flex; flex-direction:column; }
.folha-comentarios-topo { padding:14px; border-bottom:1px solid #ffffff1a; display:flex; align-items:center; justify-content:space-between; font-weight:bold; }
.folha-comentarios-topo span { cursor:pointer; color:#888; font-size:20px; }
.lista-comentarios { flex:1; overflow-y:auto; padding:12px 14px; font-size:13px; }
.comentario { margin-bottom:10px; }
.comentario b { color:#ffffff; }
.caixa-comentar { display:flex; gap:6px; padding:10px 14px calc(10px + env(safe-area-inset-bottom)); border-top:1px solid #ffffff1a; }
.caixa-comentar input { flex:1; padding:10px; border-radius:20px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; font-size:13px; }
.caixa-comentar button { padding:9px 14px; border-radius:20px; border:none; background:#1a1a1a; color:#f2f2f2; cursor:pointer; font-size:12px; }
.painel-admin { position:fixed; top:56px; left:10px; right:10px; z-index:60; background:#0d0d0df5; border:1px solid #ffffff33; border-radius:14px; padding:0; font-size:13px; overflow:hidden; display:none; max-height:80vh; }
.painel-admin.aberto { display:block; }
.painel-admin-corpo { max-height:70vh; overflow-y:auto; }
.painel-admin-cabecalho { display:flex; align-items:center; justify-content:space-between; padding:12px 14px; }
.painel-admin-cabecalho b { font-size:13px; letter-spacing:0.3px; }
.painel-admin-abas { display:flex; gap:4px; padding:0 10px; overflow-x:auto; border-bottom:1px solid #ffffff1a; }
.painel-admin-abas button { flex-shrink:0; background:none; border:none; color:#888; padding:9px 10px; font-size:12px; cursor:pointer; border-bottom:2px solid transparent; margin:0; }
.painel-admin-abas button.ativa { color:#ffffff; border-bottom:2px solid #ffffff; font-weight:bold; }
.painel-admin-secao { display:none; padding:14px; }
.painel-admin-secao.ativa { display:block; }
.painel-admin-secao .linha-admin { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:8px; }
.painel-admin input, .painel-admin input[type=color] { padding:9px 10px; border-radius:8px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; font-size:12px; }
.painel-admin input[type=text], .painel-admin input[type=file] { flex:1; min-width:110px; }
.painel-admin button.acao { padding:9px 14px; border-radius:8px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; font-size:12px; }
.painel-admin .rotulo-campo { display:block; font-size:11px; color:#888; margin-top:10px; }
.painel-admin .resultado-admin { margin-top:6px; font-size:12px; color:#ffffff; min-height:14px; }
.painel-admin .lista-icones { display:flex; flex-wrap:wrap; gap:10px; }
.painel-admin .item-icone { display:flex; flex-direction:column; align-items:center; gap:4px; font-size:11px; color:#888; }
.painel-admin .item-icone img { width:36px; height:36px; border-radius:10px; object-fit:cover; background:#1a1a1a; }
.lightbox { display:none; position:fixed; inset:0; background:#000000ee; z-index:100; align-items:center; justify-content:center; padding:16px; }
.lightbox.aberto { display:flex; }
.lightbox img, .lightbox video { max-width:92vw; max-height:88vh; border-radius:10px; }
.fechar-lightbox { position:absolute; top:20px; right:20px; color:#fff; font-size:28px; cursor:pointer; }
</style></head>
<body>
<div class="topo">
  <a href="/inicio" class="voltar">&#8592;</a>
  <span class="titulo-topo">Social CPA</span>
  {botao_engrenagem}
</div>
<div class="container">
  {painel_admin}
  <div class="feed-tela-cheia" id="feed"></div>
</div>
<div class="modal-postar" id="modalPostar">
  <div class="modal-postar-conteudo">
    <div class="modal-postar-topo"><b>Nova publicacao</b><span onclick="fecharModalPostar()">&times;</span></div>
    <div class="caixa-postar" style="border:none;padding:0;margin:0;">
      <textarea id="textoPost" placeholder="No que voce esta pensando?"></textarea>
      <input type="file" id="imagemPost" accept="image/*">
      <input type="text" id="linkImagemPost" placeholder="Link de imagem (opcional, se nao for enviar arquivo)">
      <input type="text" id="videoPost" placeholder="Link de video (Discord ou outro, opcional)">
      <br><button onclick="publicar()">Postar</button>
    </div>
  </div>
</div>
<div class="nav-inferior">
  <div class="nav-item nav-mais" onclick="abrirModalPostar()">+</div>
  <a href="/painel" class="nav-item"><img class="nav-icone" src="{icone_jarvis_nav}"><span>CHAT CPA</span></a>
  <a href="/perfil/{usuario}" class="nav-item"><img class="nav-icone" src="{avatar_usuario_nav}"><span>Perfil</span></a>
</div>
<div class="lightbox" id="lightbox" onclick="fecharLightbox()">
  <span class="fechar-lightbox">&times;</span>
  <div id="lightboxConteudo"></div>
</div>
<div class="folha-comentarios" id="folhaComentarios" onclick="fecharFolhaComentarios()">
  <div class="folha-comentarios-conteudo" onclick="event.stopPropagation()">
    <div class="folha-comentarios-topo"><b>Comentarios</b><span onclick="fecharFolhaComentarios()">&times;</span></div>
    <div class="lista-comentarios" id="listaComentariosFolha"></div>
    <div class="caixa-comentar">
      <input type="text" id="campoNovoComentario" placeholder="Adicione um comentario..." onkeydown="if(event.key==='Enter'){enviarComentario();}">
      <button onclick="enviarComentario()">Enviar</button>
    </div>
  </div>
</div>
<script>
const usuarioLogado = "{usuario}";
const ICONE_CORACAO = '<svg viewBox="0 0 24 24" stroke="#fff" stroke-width="1.6" fill="none"><path d="M12 21s-7.5-4.6-10.1-9.1C.4 8.8 1.7 5 5.4 4.3c2-.4 3.9.5 5 2.1.9-1.6 2.9-2.5 5-2.1 3.7.7 5 4.5 3.5 7.6C19.5 16.4 12 21 12 21z"/></svg>';
const ICONE_COMENTAR = '<svg viewBox="0 0 24 24" fill="#fff"><path d="M21 11.5a8.4 8.4 0 01-8.9 8.4 9 9 0 01-3.6-.7L3 21l1.8-5.3a8.4 8.4 0 01-.8-3.6A8.4 8.4 0 0112.5 3a8.6 8.6 0 018.5 8.5z"/></svg>';
const ICONE_SALVAR = '<svg viewBox="0 0 24 24" stroke="#fff" stroke-width="1.6" fill="none"><path d="M6 3h12a1 1 0 011 1v17l-7-4-7 4V4a1 1 0 011-1z"/></svg>';
let sombraAudioLiberada = false;
function abrirLightbox(src, tipo, ev) {
    if (ev) ev.stopPropagation();
    const el = document.getElementById("lightboxConteudo");
    el.innerHTML = tipo === "video" ? "<video src='" + src + "' controls autoplay></video>" : "<img src='" + src + "'>";
    document.getElementById("lightbox").classList.add("aberto");
}
function fecharLightbox() {
    document.getElementById("lightbox").classList.remove("aberto");
    document.getElementById("lightboxConteudo").innerHTML = "";
}
let ultimosPostsCarregados = [];
async function carregarFeed() {
    const resposta = await fetch("/rede/feed");
    const posts = await resposta.json();
    ultimosPostsCarregados = posts;
    const div = document.getElementById("feed");
    if (!posts.length) { div.innerHTML = '<div class="feed-vazio">Ainda nao tem publicacoes. Toque no + para postar algo.</div>'; return; }
    div.innerHTML = "";
    posts.forEach(p => {
        const bloco = document.createElement("div");
        bloco.className = "post";
        const selo = p.verificado ? (p.selo_html || '') : '';
        let midiaHtml = "";
        if (p.imagem) midiaHtml = "<div class='post-midia-wrap'><img class='post-imagem' src='" + p.imagem + "' onclick=\\"abrirLightbox('" + p.imagem + "','imagem',event)\\"></div>";
        else if (p.video) midiaHtml = "<div class='post-midia-wrap'><video controlsList='nodownload noremoteplayback' disablePictureInPicture oncontextmenu='return false' playsinline muted loop preload='auto' src='" + p.video + "'></video></div>";
        else midiaHtml = "<div class='post-sem-midia'></div>";
        let html = midiaHtml;
        html += '<div class="acoes-laterais">';
        html += '<div class="acao-lateral ' + (p.curtido ? 'curtido' : '') + '" onclick="curtir(' + p.id + ')">' + ICONE_CORACAO + '<span>' + p.curtidas + '</span></div>';
        html += '<div class="acao-lateral" onclick="mostrarComentarios(' + p.id + ')">' + ICONE_COMENTAR + '<span>' + p.comentarios.length + '</span></div>';
        html += '<div class="acao-lateral ' + (p.salvo ? 'salvo' : '') + '" onclick="salvarPost(' + p.id + ')">' + ICONE_SALVAR + '<span>' + (p.salvo ? 'Salvo' : 'Salvar') + '</span></div>';
        html += '</div>';
        html += '<div class="post-rodape"><div class="post-cabecalho"><a href="/perfil/' + p.usuario + '"><img src="' + p.avatar + '">' + p.usuario + selo + (p.tag_html || '');
        if (p.usuario !== usuarioLogado) html += '<button class="botao-seguir-lateral" onclick="event.preventDefault();seguir(\\'' + p.usuario + '\\')">' + (p.seguindo ? 'Seguindo' : 'Seguir') + '</button>';
        html += '</a></div>';
        if (p.texto) html += '<div class="post-texto"></div>';
        html += '</div>';
        bloco.innerHTML = html;
        if (p.texto) bloco.querySelector(".post-texto").textContent = p.texto;
        bloco.dataset.postId = p.id;
        div.appendChild(bloco);
    });
    configurarAutoplayFeed();
}
function abrirFolhaComentarios(id) {
    const post = ultimosPostsCarregados.find(p => p.id === id);
    if (!post) return;
    const lista = document.getElementById("listaComentariosFolha");
    lista.innerHTML = "";
    post.comentarios.forEach(c => {
        const linha = document.createElement("div");
        linha.className = "comentario";
        const b = document.createElement("b"); b.textContent = c.usuario + ": ";
        linha.appendChild(b);
        linha.appendChild(document.createTextNode(c.texto));
        lista.appendChild(linha);
    });
    if (!post.comentarios.length) lista.innerHTML = '<div style="color:#777;">Nenhum comentario ainda.</div>';
    document.getElementById("folhaComentarios").dataset.postId = id;
    document.getElementById("folhaComentarios").classList.add("aberta");
}
function fecharFolhaComentarios() { document.getElementById("folhaComentarios").classList.remove("aberta"); }
function mostrarComentarios(id) { abrirFolhaComentarios(id); }
function abrirModalPostar() { document.getElementById("modalPostar").classList.add("aberto"); }
function fecharModalPostar() { document.getElementById("modalPostar").classList.remove("aberto"); }
// ---------- Rolagem estilo feed de video: quando um video entra na tela ele toca
// (mudo por padrao - o som so liga depois que a pessoa toca na tela uma vez, pra
// nao "ligar" audio sozinho); quando sai, para. So um video toca por vez. ----------
let observadorVideos = null;
function configurarAutoplayFeed() {
    if (observadorVideos) observadorVideos.disconnect();
    observadorVideos = new IntersectionObserver((entradas) => {
        entradas.forEach(entrada => {
            const video = entrada.target;
            if (entrada.isIntersecting && entrada.intersectionRatio >= 0.6) {
                document.querySelectorAll(".post video").forEach(v => { if (v !== video) { v.pause(); } });
                video.muted = !sombraAudioLiberada;
                video.play().catch(() => {});
            } else {
                video.pause();
            }
        });
    }, { threshold: [0, 0.6, 1] });
    document.querySelectorAll(".post video").forEach(v => {
        v.observe = null;
        v.addEventListener("click", () => {
            sombraAudioLiberada = true;
            v.muted = false;
            v.play().catch(() => {});
        });
        observadorVideos.observe(v);
    });
}
async function publicar() {
    const botao = document.querySelector(".caixa-postar button");
    const texto = document.getElementById("textoPost").value.trim();
    const arquivo = document.getElementById("imagemPost").files[0];
    const linkImagem = document.getElementById("linkImagemPost").value.trim();
    const video = document.getElementById("videoPost").value.trim();
    if (!texto && !arquivo && !linkImagem && !video) {
        alert("Escreva algo, escolha uma foto ou cole um link antes de postar.");
        return;
    }
    const form = new FormData();
    form.append("texto", texto);
    if (arquivo) form.append("imagem", arquivo);
    if (linkImagem) form.append("imagem_link", linkImagem);
    if (video) form.append("video", video);
    botao.disabled = true;
    botao.textContent = "Postando...";
    try {
        const resposta = await fetch("/rede/postar", { method: "POST", body: form });
        if (!resposta.ok) {
            const dados = await resposta.json().catch(() => ({}));
            alert(dados.erro || "Nao consegui postar. Sua sessao pode ter expirado - tenta sair e logar de novo.");
            return;
        }
        document.getElementById("textoPost").value = "";
        document.getElementById("imagemPost").value = "";
        document.getElementById("linkImagemPost").value = "";
        document.getElementById("videoPost").value = "";
        fecharModalPostar();
        carregarFeed();
    } catch (erro) {
        alert("Falha de conexao ao postar. Verifica sua internet e tenta de novo.");
    } finally {
        botao.disabled = false;
        botao.textContent = "Postar";
    }
}
async function curtir(id) { await fetch("/rede/curtir", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({post_id: id}) }); carregarFeed(); }
async function enviarComentario() {
    const folha = document.getElementById("folhaComentarios");
    const id = parseInt(folha.dataset.postId, 10);
    const campo = document.getElementById("campoNovoComentario");
    const texto = campo.value.trim();
    if (!texto || !id) return;
    campo.disabled = true;
    try {
        const resposta = await fetch("/rede/comentar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({post_id: id, texto: texto}) });
        const dados = await resposta.json().catch(() => ({}));
        if (!dados.ok) { alert(dados.erro || "Nao consegui enviar o comentario."); return; }
        campo.value = "";
        await carregarFeed();
        abrirFolhaComentarios(id);
    } catch (erro) {
        alert("Falha de conexao ao comentar. Verifica sua internet e tenta de novo.");
    } finally {
        campo.disabled = false;
        campo.focus();
    }
}
async function seguir(alvo) { await fetch("/rede/seguir", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo: alvo}) }); carregarFeed(); }
async function salvarPost(id) { await fetch("/rede/salvar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({post_id: id}) }); carregarFeed(); }
function mudarAbaAdmin(nome, botao) {
    document.querySelectorAll(".painel-admin-abas button").forEach(b => b.classList.remove("ativa"));
    document.querySelectorAll(".painel-admin-secao").forEach(s => s.classList.remove("ativa"));
    botao.classList.add("ativa");
    document.getElementById("secaoAdmin-" + nome).classList.add("ativa");
}
async function verificar() {
    const alvo = document.getElementById("alvoVerificar").value.trim();
    const resultado = document.getElementById("resultadoVerificar");
    if (!alvo) { resultado.textContent = "Digite um email ou ID."; return; }
    const resposta = await fetch("/rede/verificar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo: alvo}) });
    const dados = await resposta.json();
    resultado.textContent = dados.ok ? (dados.verificado ? "Verificado!" : "Selo removido.") : (dados.erro || "Erro.");
    if (dados.ok) carregarFeed();
}
async function criarTag() {
    const nome = document.getElementById("tagNome").value.trim();
    const resultado = document.getElementById("resultadoTag");
    if (!nome) { resultado.textContent = "Digite o nome da tag."; return; }
    const form = new FormData();
    form.append("nome", nome);
    form.append("cor", document.getElementById("tagCor").value);
    const arquivo = document.getElementById("tagFoto").files[0];
    if (arquivo) form.append("foto", arquivo);
    const resposta = await fetch("/rede/criar_tag", { method: "POST", body: form });
    const dados = await resposta.json();
    resultado.textContent = dados.ok ? "Tag criada!" : (dados.erro || "Erro.");
}
async function atribuirTag() {
    const alvo = document.getElementById("tagAlvo").value.trim();
    const tag = document.getElementById("tagNomeAtribuir").value.trim();
    const resultado = document.getElementById("resultadoAtribuir");
    if (!alvo) { resultado.textContent = "Digite um email ou ID."; return; }
    const resposta = await fetch("/rede/atribuir_tag", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo, tag}) });
    const dados = await resposta.json();
    resultado.textContent = dados.ok ? "Tag atribuida!" : (dados.erro || "Erro.");
    if (dados.ok) carregarFeed();
}
async function enviarConfig(chave, idInput, idResultado, elementoInput) {
    const input = elementoInput || document.getElementById(idInput);
    const arquivo = input.files[0];
    const resultado = document.getElementById(idResultado);
    if (!arquivo) { resultado.textContent = "Escolha uma imagem."; return; }
    const form = new FormData();
    form.append("chave", chave);
    form.append("arquivo", arquivo);
    resultado.textContent = "Enviando...";
    const resposta = await fetch("/admin/config", { method: "POST", body: form });
    const dados = await resposta.json();
    resultado.textContent = dados.ok ? "Salvo!" : (dados.erro || "Erro.");
    if (dados.ok && dados.url) {
        const previa = document.getElementById("previa" + chave.split("_").map(p => p[0].toUpperCase()+p.slice(1)).join(""));
    }
}
async function definirIdAdmin() {
    const alvo = document.getElementById("idAlvo").value.trim();
    const novoId = document.getElementById("idNovo").value.trim();
    const resultado = document.getElementById("resultadoId");
    if (!alvo || !novoId) { resultado.textContent = "Preencha o email/ID da pessoa e o novo ID."; return; }
    resultado.textContent = "Salvando...";
    const resposta = await fetch("/admin/definir_id", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo, novo_id: novoId}) });
    const dados = await resposta.json();
    resultado.textContent = dados.ok ? (dados.usuario + " agora e #" + dados.id_publico) : (dados.erro || "Erro.");
    if (dados.ok) carregarFeed();
}
async function buscarConversasZap() {
    const frase = document.getElementById("zapBuscaFrase").value.trim();
    const resposta = await fetch("/admin/zap/conversas?frase=" + encodeURIComponent(frase));
    const conversas = await resposta.json();
    const lista = document.getElementById("listaConversasZap");
    lista.innerHTML = "";
    conversas.forEach(c => {
        const item = document.createElement("div");
        item.style.cssText = "padding:6px 0;border-bottom:1px solid #ffffff1a;cursor:pointer;";
        item.textContent = c.participantes.join(" + ") + (c.frase ? " (criptografia: " + c.frase + ")" : "");
        item.onclick = () => verHistoricoZap(c.conversa);
        lista.appendChild(item);
    });
    if (!conversas.length) lista.textContent = "Nenhuma conversa encontrada.";
}
async function verHistoricoZap(conversa) {
    const resposta = await fetch("/admin/zap/historico/" + encodeURIComponent(conversa));
    const dados = await resposta.json();
    const div = document.getElementById("historicoZap");
    div.innerHTML = "<b>Historico:</b><br>";
    dados.mensagens.forEach(m => {
        const linha = document.createElement("div");
        linha.textContent = m.remetente + ": " + m.conteudo;
        div.appendChild(linha);
    });
}
carregarFeed();
</script>

</body></html>
"""

PAGINA_PERFIL = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>{nome_usuario} - Social CPA</title>
<style>
""" + ESTILO_COMUM + """
body { height:100vh; overflow-y:auto; }
.topo { position:sticky; top:0; background:#000000; padding:14px 16px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; gap:12px; }
.voltar { color:#ffffff; text-decoration:none; font-size:20px; }
.banner { width:100%; height:140px; object-fit:cover; background:#0d0d0d; }
.container { max-width:600px; margin:0 auto; padding:16px; }
.cabecalho-perfil { display:flex; align-items:center; gap:20px; margin-bottom:14px; flex-wrap:wrap; margin-top:-40px; }
.cabecalho-perfil img.avatar-grande { width:90px; height:90px; border-radius:50%; object-fit:cover; border:3px solid #000000; background:#0d0d0d; }
.bio-perfil { font-size:14px; color:#cccccc; margin-bottom:16px; white-space:pre-wrap; }
.id-publico { font-size:12px; color:#888; margin-top:2px; letter-spacing:0.5px; }
.stats { display:flex; gap:20px; margin-top:8px; font-size:14px; }
.stats b { display:block; font-size:16px; }
.botao-seguir { padding:8px 18px; border-radius:10px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; margin-top:10px; transition:opacity 0.15s ease; }
.botao-seguir:hover { opacity:0.85; }
.botao-seguir.ativo { background:#1a1a1a; color:#f2f2f2; border:1px solid #ffffff33; }
.editar-perfil { background:#1a1a1a; border:1px solid #ffffff33; border-radius:10px; padding:14px; margin-bottom:20px; font-size:13px; }
.editar-perfil input, .editar-perfil textarea { width:100%; padding:8px; margin-top:6px; border-radius:6px; border:1px solid #ffffff22; background:#000; color:#f2f2f2; font-family:inherit; }
.editar-perfil button { margin-top:8px; padding:9px 16px; border-radius:8px; border:none; background:#ffffff; color:#000; font-weight:bold; cursor:pointer; transition:opacity 0.15s ease; }
.editar-perfil button:hover { opacity:0.85; }
.editar-perfil .linha-id { display:flex; gap:6px; align-items:center; margin-top:10px; }
.editar-perfil .linha-id input { margin-top:0; }
.editar-perfil .btn-secundario { background:#000; color:#f2f2f2; border:1px solid #ffffff33; padding:8px 12px; border-radius:8px; cursor:pointer; font-size:12px; white-space:nowrap; }
.editar-perfil .msg-id { font-size:11px; margin-top:4px; color:#888; }
.grade { display:grid; grid-template-columns: repeat(3, 1fr); gap:4px; }
.grade-item { aspect-ratio:1; background:#0d0d0d; border-radius:4px; overflow:hidden; display:flex; align-items:center; justify-content:center; }
.grade-item img, .grade-item video { width:100%; height:100%; object-fit:cover; }
.grade-item.sem-midia { font-size:12px; color:#aaaaaa; padding:8px; text-align:center; }
@media (max-width:480px) { .container { padding:10px; } .cabecalho-perfil img.avatar-grande { width:70px; height:70px; } .banner { height:100px; } }
</style></head>
<body>
<div class="topo"><a href="/rede" class="voltar">&#8592;</a><span>Perfil</span></div>
{banner_html}
<div class="container">
  <div class="cabecalho-perfil">
    <img class="avatar-grande" src="{avatar_url}">
    <div>
      <h2 style="margin:0;">{nome_usuario} {selo}</h2>
      <div class="id-publico">ID #{id_publico}</div>
      <div class="stats"><div><b>{qtd_posts}</b>posts</div><div><b>{qtd_seguidores}</b>seguidores</div><div><b>{qtd_seguindo}</b>seguindo</div></div>
      {botao_seguir}
    </div>
  </div>
  {bio_html}
  {editor_perfil}
  <div class="grade">{itens_grade}</div>
</div>
<script>
async function seguirPerfil(alvo) {
    await fetch("/rede/seguir", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo: alvo}) });
    location.reload();
}
async function salvarPerfil() {
    const form = new FormData();
    form.append("bio", document.getElementById("novaBio").value.trim());
    const arquivoFoto = document.getElementById("novoAvatarArquivo").files[0];
    const arquivoBanner = document.getElementById("novoBannerArquivo").files[0];
    if (arquivoFoto) form.append("foto_perfil", arquivoFoto);
    if (arquivoBanner) form.append("banner", arquivoBanner);
    await fetch("/perfil/editar", { method: "POST", body: form });
    location.reload();
}
async function mudarId() {
    const msg = document.getElementById("msgId");
    const novoId = document.getElementById("campoNovoId").value.trim();
    msg.style.color = "#888";
    msg.textContent = "Salvando...";
    const r = await fetch("/perfil/mudar_id", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({novo_id: novoId}) });
    const dados = await r.json();
    if (dados.ok) { msg.style.color = "#7CFC9B"; msg.textContent = "ID atualizado!"; setTimeout(() => location.reload(), 700); }
    else { msg.style.color = "#ff6666"; msg.textContent = dados.erro || "Nao foi possivel mudar o ID."; }
}
</script>
</body></html>
"""

# ---------- SUPORTE ----------
PAGINA_SUPORTE = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Suporte - CHAT CPA</title>
<style>
""" + ESTILO_COMUM + """
html, body { height:100dvh; }
body { display:flex; flex-direction:column; overflow:hidden; }
.topo { flex-shrink:0; padding:14px 16px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; justify-content:space-between; gap:10px; }
.topo-esquerda { display:flex; align-items:center; gap:12px; }
.voltar { color:#ffffff; text-decoration:none; font-size:20px; }
.relogio-topo { font-size:13px; color:#888; }
.container { flex:1; display:flex; overflow:hidden; min-height:0; }
.lista-tickets { width:220px; border-right:1px solid #ffffff22; overflow-y:auto; padding:10px; flex-shrink:0; }
.item-ticket { padding:10px; border-radius:8px; margin-bottom:6px; background:#0d0d0d; border:1px solid #ffffff22; cursor:pointer; font-size:13px; }
.item-ticket:hover, .item-ticket.ativo { background:#1a1a1a; }
.vazio-lista { color:#666; font-size:12px; padding:10px; text-align:center; }
.chat-suporte { flex:1; display:flex; flex-direction:column; min-height:0; min-width:0; }
.mensagens-suporte { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:10px; min-height:0; }
.vazio-chat { flex:1; display:flex; align-items:center; justify-content:center; color:#666; font-size:13px; text-align:center; padding:0 30px; }
.msg-s { max-width:75%; padding:10px 14px; border-radius:14px; font-size:14px; line-height:1.4; }
.msg-s.eu { align-self:flex-end; background:#3ddc6a33; border:1px solid #3ddc6a55; border-bottom-right-radius:4px; }
.msg-s.outro { align-self:flex-start; background:#0d0d0d; border:1px solid #ffffff22; border-bottom-left-radius:4px; }
.msg-s .remetente-nome { display:block; font-size:11px; color:#888; margin-bottom:3px; }
.area-input-suporte { flex-shrink:0; padding:12px 14px calc(12px + env(safe-area-inset-bottom)); border-top:1px solid #ffffff22; display:flex; gap:8px; }
.area-input-suporte input { flex:1; padding:12px 14px; border-radius:20px; border:1px solid #ffffff33; background:#0d0d0d; color:#f2f2f2; font-size:14px; }
.area-input-suporte button { padding:12px 18px; border-radius:20px; border:none; background:#ffffff; color:#000; font-weight:bold; cursor:pointer; font-size:13px; }
.painel-admin-suporte { margin:12px 16px 0; background:#0d0d0d; border:1px solid #ffffff33; border-radius:12px; padding:14px; font-size:13px; flex-shrink:0; }
.painel-admin-suporte b { display:block; margin-bottom:8px; }
.painel-admin-suporte .linha-admin { display:flex; flex-wrap:wrap; gap:6px; }
.painel-admin-suporte input { flex:1; min-width:140px; padding:9px 10px; border-radius:8px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; font-size:12px; }
.painel-admin-suporte button.acao { padding:9px 14px; border-radius:8px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; font-size:12px; }
.painel-admin-suporte .resultado-admin { margin-top:6px; font-size:12px; min-height:14px; }
@media (max-width:720px) {
  .container { flex-direction:column; }
  .lista-tickets { width:100%; height:110px; border-right:none; border-bottom:1px solid #ffffff22; display:flex; gap:8px; overflow-x:auto; overflow-y:hidden; }
  .item-ticket { flex-shrink:0; white-space:nowrap; margin-bottom:0; }
}
</style></head>
<body>
<div class="topo">
  <div class="topo-esquerda"><a href="/inicio" class="voltar">&#8592;</a><span>Suporte</span></div>
  <span class="relogio-topo" id="relogioSuporte">--:--</span>
</div>
{painel_admin}
<div class="container">
  <div class="lista-tickets" id="listaTickets" style="display:none;"></div>
  <div class="chat-suporte">
    <div class="mensagens-suporte" id="mensagensSuporte"></div>
    <div class="area-input-suporte" id="areaAbrirChamado" style="display:none;">
      <button onclick="abrirChamado()" style="width:100%;padding:12px 18px;border-radius:20px;border:none;background:#ffffff;color:#000;font-weight:bold;cursor:pointer;font-size:13px;">Abrir chamado</button>
    </div>
    <div class="area-input-suporte">
      <input type="text" id="campoSuporte" placeholder="Escreva sua duvida...">
      <button onclick="enviarSuporte()">Enviar</button>
    </div>
  </div>
</div>
<script>
const usuarioAtual = "{usuario}";
const ehAgente = {eh_agente};
let ticketAtualId = null;

function atualizarRelogioSuporte() {
    const agora = new Date();
    const h = String(agora.getHours()).padStart(2,'0');
    const m = String(agora.getMinutes()).padStart(2,'0');
    document.getElementById("relogioSuporte").textContent = h + ":" + m;
}
atualizarRelogioSuporte();
setInterval(atualizarRelogioSuporte, 1000);

function renderizarMensagensSuporte(mensagens) {
    const div = document.getElementById("mensagensSuporte");
    div.innerHTML = "";
    if (!mensagens.length) {
        div.innerHTML = '<div class="vazio-chat">' + (ehAgente ? "Escolha um chamado na lista pra ver a conversa." : "Manda sua duvida aqui embaixo que a gente responde.") + '</div>';
        return;
    }
    mensagens.forEach(m => {
        const bolha = document.createElement("div");
        bolha.className = "msg-s " + (m.remetente === usuarioAtual ? "eu" : "outro");
        if (m.remetente !== usuarioAtual) {
            const nome = document.createElement("span");
            nome.className = "remetente-nome";
            nome.textContent = m.remetente;
            bolha.appendChild(nome);
        }
        bolha.appendChild(document.createTextNode(m.texto));
        div.appendChild(bolha);
    });
    div.scrollTop = div.scrollHeight;
}

async function carregarMeuTicket() {
    const resposta = await fetch("/suporte/meu_ticket");
    const dados = await resposta.json();
    ticketAtualId = dados.ticket_id;
    renderizarMensagensSuporte(dados.mensagens || []);
    if (!ehAgente) {
        document.getElementById("areaAbrirChamado").style.display = ticketAtualId ? "none" : "flex";
    }
}

async function abrirChamado() {
    const resposta = await fetch("/suporte/abrir_chamado", { method: "POST" });
    const dados = await resposta.json();
    if (!dados.ok) { alert(dados.erro || "Nao foi possivel abrir o chamado."); return; }
    ticketAtualId = dados.ticket_id;
    document.getElementById("areaAbrirChamado").style.display = "none";
    carregarMeuTicket();
}

async function carregarListaTickets() {
    const resposta = await fetch("/suporte/tickets");
    const tickets = await resposta.json();
    const div = document.getElementById("listaTickets");
    div.style.display = "flex";
    div.innerHTML = "";
    if (!tickets.length) { div.innerHTML = '<div class="vazio-lista">Nenhum chamado aberto.</div>'; return; }
    tickets.forEach(t => {
        const item = document.createElement("div");
        item.className = "item-ticket" + (t.id === ticketAtualId ? " ativo" : "");
        item.textContent = t.usuario + (t.atendente ? " (" + t.atendente + ")" : " (aberto)");
        item.onclick = () => abrirTicket(t.id);
        div.appendChild(item);
    });
}
async function abrirTicket(id) {
    ticketAtualId = id;
    const resposta = await fetch("/suporte/ticket/" + id);
    const dados = await resposta.json();
    renderizarMensagensSuporte(dados.mensagens || []);
    carregarListaTickets();
}

async function enviarSuporte() {
    const campo = document.getElementById("campoSuporte");
    const texto = campo.value.trim();
    if (!texto) return;
    campo.value = "";
    if (ehAgente && ticketAtualId) {
        await fetch("/suporte/responder", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ticket_id: ticketAtualId, texto}) });
        abrirTicket(ticketAtualId);
    } else if (ehAgente && !ticketAtualId) {
        alert("Escolha um chamado na lista antes de responder.");
    } else {
        await fetch("/suporte/enviar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({texto}) });
        carregarMeuTicket();
    }
}
document.getElementById("campoSuporte").addEventListener("keydown", e => { if (e.key === "Enter") enviarSuporte(); });

async function adicionarAgente() {
    const idPublico = document.getElementById("idAgente").value.trim();
    const resultado = document.getElementById("resultadoAgente");
    if (!idPublico) { resultado.textContent = "Digite o ID da conta."; return; }
    resultado.textContent = "Adicionando...";
    const resposta = await fetch("/suporte/agente", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id_publico: idPublico}) });
    const dados = await resposta.json();
    resultado.textContent = dados.ok ? ("Atendente adicionado: " + dados.usuario) : (dados.erro || "Erro.");
}

if (ehAgente) {
    carregarListaTickets();
    setInterval(carregarListaTickets, 4000);
} else {
    carregarMeuTicket();
    setInterval(carregarMeuTicket, 4000);
}
</script>
</body></html>
"""

# ---------- CONTA BLOQUEADA (moderacao) ----------
PAGINA_BLOQUEADO = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CHAT CPA</title><style>""" + ESTILO_COMUM + """
body { height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; padding:24px; }
.icone-bloqueio { font-size:48px; margin-bottom:16px; }
h2 { margin:0 0 10px; }
p { color:#999; max-width:340px; }
a { color:#fff; margin-top:18px; text-decoration:underline; }
</style></head><body>
<div class="icone-bloqueio">&#128683;</div>
<h2>Conta bloqueada no ZAP</h2>
<p>O bot do CHAT CPA identificou envios repetidos de conteudo proibido (pornografia, conteudo adulto ou conteudo de terror/ameaca) e bloqueou esta conta para o ZAP. Se acha que foi um engano, abra um chamado no Suporte.</p>
<a href="/suporte">Ir para o Suporte</a><br><a href="/inicio">Voltar ao inicio</a>
</body></html>
"""

# ---------- ZAP ----------
PAGINA_ZAP = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>ZAP</title>
<style>
""" + ESTILO_COMUM + """
body { display:flex; height:100vh; overflow:hidden; }
.sidebar-zap { width:280px; background:#0d0d0d; border-right:1px solid #ffffff22; display:flex; flex-direction:column; flex-shrink:0; }
.topo-zap-lista { padding:14px 16px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; justify-content:space-between; }
.topo-zap-lista a { color:#fff; text-decoration:none; font-size:18px; }
.btn-add-contato { background:#1a1a1a; border:1px solid #ffffff33; color:#fff; border-radius:8px; padding:6px 10px; cursor:pointer; font-size:18px; line-height:1; }
.lista-contatos { flex:1; overflow-y:auto; }
.item-contato { display:flex; align-items:center; gap:10px; padding:12px 14px; cursor:pointer; border-bottom:1px solid #ffffff11; }
.item-contato:hover, .item-contato.ativo { background:#1a1a1a; }
.item-contato img { width:38px; height:38px; border-radius:50%; object-fit:cover; }
.item-contato .nome { font-size:14px; }
.item-contato .idc { font-size:11px; color:#888; }
.vazio-contatos { padding:20px; color:#777; font-size:13px; text-align:center; }
.chat-area { flex:1; display:flex; flex-direction:column; min-width:0; }
.topo-chat { padding:14px 18px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.topo-chat img { width:34px; height:34px; border-radius:50%; object-fit:cover; flex-shrink:0; }
.topo-chat-acoes { margin-left:auto; display:flex; gap:6px; flex-wrap:wrap; }
.botao-voltar-lista { display:none; font-size:20px; cursor:pointer; flex-shrink:0; }
.badge-cripto { font-size:11px; padding:4px 10px; border-radius:12px; background:#0d0d0d; border:1px solid #3ddc6a55; color:#3ddc6a; display:none; }
.badge-cripto.ativo { display:inline-block; }
.msgs-zap { flex:1; overflow-y:auto; padding:18px; display:flex; flex-direction:column; gap:10px; }
.bolha { max-width:65%; padding:10px 14px; border-radius:12px; line-height:1.4; font-size:14px; position:relative; }
.bolha.minha { align-self:flex-end; background:#3ddc6a33; border:1px solid #3ddc6a55; }
.bolha.dele { align-self:flex-start; background:#0d0d0d; border:1px solid #ffffff22; }
.bolha.sistema { align-self:center; background:transparent; color:#888; font-size:12px; border:none; }
.bolha img, .bolha video { max-width:220px; border-radius:8px; margin-top:4px; display:block; }
.bolha audio { margin-top:4px; }
.bolha .denunciar { display:block; margin-top:6px; font-size:10px; color:#888; cursor:pointer; text-decoration:underline; }
.sem-conversa { flex:1; display:flex; align-items:center; justify-content:center; color:#666; }
.area-input-zap { padding:12px 16px calc(12px + env(safe-area-inset-bottom)); border-top:1px solid #ffffff22; display:flex; gap:8px; align-items:center; }
.area-input-zap input[type=text] { flex:1; padding:12px 14px; border-radius:20px; border:1px solid #ffffff33; background:#0d0d0d; color:#f2f2f2; font-size:14px; }
.area-input-zap button, .area-input-zap label { background:#1a1a1a; border:1px solid #ffffff33; color:#fff; border-radius:50%; width:40px; height:40px; display:flex; align-items:center; justify-content:center; cursor:pointer; font-size:16px; flex-shrink:0; }
.area-input-zap button.enviar { background:#fff; color:#000; }
.area-input-zap button.gravando { background:#ff3b3b; }
@media (max-width:720px) {
  .sidebar-zap { position:fixed; z-index:20; height:100vh; transition:margin-left 0.2s; }
  .sidebar-zap.recolhida { margin-left:-280px; }
  .bolha { max-width:85%; }
  .botao-voltar-lista { display:block; }
  .topo-chat-acoes { width:100%; margin-left:0; justify-content:flex-start; }
}
.modal-chamada { display:none; position:fixed; inset:0; background:#000000f2; z-index:200; align-items:center; justify-content:center; flex-direction:column; color:#fff; text-align:center; padding:16px; }
.modal-chamada.aberto { display:flex; }
.modal-chamada img { width:96px; height:96px; border-radius:50%; object-fit:cover; margin-bottom:16px; border:2px solid #ffffff33; }
.modal-chamada .status-chamada { color:#888; margin-bottom:30px; font-size:14px; }
.modal-chamada .botoes-chamada { display:flex; gap:20px; flex-wrap:wrap; justify-content:center; }
.botao-chamada-circulo { width:56px; height:56px; border-radius:50%; border:none; font-size:22px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
.botao-chamada-circulo.aceitar { background:#3ddc6a; color:#000; }
.botao-chamada-circulo.recusar, .botao-chamada-circulo.encerrar { background:#ff3b3b; color:#fff; }
.video-remoto-chamada { display:none; width:100%; height:100%; position:absolute; inset:0; object-fit:cover; background:#000; }
.video-remoto-chamada.ativo { display:block; }
.video-local-chamada { display:none; position:absolute; bottom:120px; right:20px; width:100px; height:140px; border-radius:12px; object-fit:cover; border:2px solid #ffffff44; z-index:2; cursor:pointer; background:#111; }
.video-local-chamada.ativo { display:block; }
.video-local-chamada.tela-cheia { bottom:0; right:0; top:0; left:0; width:100%; height:100%; border-radius:0; border:none; z-index:1; }
.video-remoto-chamada.reduzido { position:absolute; bottom:120px; right:20px; width:100px; height:140px; border-radius:12px; border:2px solid #ffffff44; z-index:2; }
</style></head>
<body>
<div class="sidebar-zap" id="sidebarZap">
  <div class="topo-zap-lista"><a href="/inicio">&#8592;</a><b>ZAP</b><span class="btn-add-contato" onclick="menuAdicionar()">+</span></div>
  <div class="lista-contatos" id="listaContatos"><div class="vazio-contatos">Carregando...</div></div>
  <div style="padding:10px 14px;border-top:1px solid #ffffff1a;">
    <div class="item-contato" style="padding:8px 0;cursor:pointer;color:#888;" onclick="window.location.href='/zap/grupos'">&#128101; Meus grupos</div>
  </div>
</div>
<div class="chat-area">
  <div class="sem-conversa" id="semConversa">Adicione um contato pelo ID (#) para comecar a conversar.</div>
  <div id="conversaAberta" style="display:none; flex:1; display:flex; flex-direction:column; min-height:0;">
    <div class="topo-chat">
      <span class="botao-voltar-lista" id="botaoVoltarLista" onclick="voltarParaLista()" title="Voltar para a lista de conversas">&#8592;</span>
      <img id="avatarChatAtual" src="">
      <div><div id="nomeChatAtual" style="font-weight:bold;"></div><div id="idChatAtual" style="font-size:11px;color:#888;"></div></div>
      <div class="topo-chat-acoes">
        <span class="badge-cripto" id="badgeCripto" onclick="ativarCriptografia()" title="Toque para trocar a criptografia" style="display:none;">&#128274; criptografado</span>
        <span class="badge-cripto" id="botaoCriptografar" onclick="ativarCriptografia()" title="Ativar criptografia" style="display:inline-block;cursor:pointer;">&#128275; criptografar</span>
        <span class="badge-cripto" id="botaoBloquear" onclick="alternarBloqueio()" style="display:inline-block;cursor:pointer;border-color:#ff6b6b55;color:#ff6b6b;">Bloquear</span>
        <span class="badge-cripto" id="botaoLigar" onclick="iniciarChamada(false)" style="display:inline-block;cursor:pointer;border-color:#3ddc6a55;color:#3ddc6a;">&#128222; Ligar</span>
        <span class="badge-cripto" id="botaoVideoChamada" onclick="iniciarChamada(true)" style="display:inline-block;cursor:pointer;border-color:#3ddc6a55;color:#3ddc6a;">&#128249; Video</span>
      </div>
    </div>
    <div class="msgs-zap" id="msgsZap"></div>
    <div class="area-input-zap">
      <label title="Imagem">&#128247;<input type="file" id="inputImagem" accept="image/*" style="display:none" onchange="enviarArquivo(this,'imagem')"></label>
      <label title="Video">&#127909;<input type="file" id="inputVideo" accept="video/*" style="display:none" onchange="enviarArquivo(this,'video')"></label>
      <button title="Gravar audio" id="botaoGravar" onclick="alternarGravacaoAudio()">&#127908;</button>
      <input type="text" id="campoZap" placeholder="Mensagem (dica: digite 'criptografia de 15/08/2000' para ativar a criptografia desta conversa)" onkeydown="if(event.key==='Enter')enviarTextoZap()">
      <button class="enviar" onclick="enviarTextoZap()">&#10148;</button>
    </div>
  </div>
</div>
<div class="modal-chamada" id="modalChamada">
  <video class="video-remoto-chamada" id="videoRemoto" autoplay playsinline onclick="alternarTelasChamada()"></video>
  <video class="video-local-chamada" id="videoLocal" autoplay playsinline muted onclick="alternarTelasChamada()"></video>
  <img id="avatarChamada" src="">
  <div id="nomeChamada" style="font-size:18px;font-weight:bold;z-index:2;"></div>
  <div class="status-chamada" id="statusChamada" style="z-index:2;">Chamando...</div>
  <div class="botoes-chamada" id="botoesChamada" style="z-index:2;"></div>
  <button id="avisoToqueChamada" onclick="liberarMidiaChamada()" style="display:none;z-index:3;position:absolute;bottom:30%;padding:12px 20px;border-radius:20px;border:none;background:#fff;color:#000;font-weight:bold;cursor:pointer;">Toque para ativar audio/video</button>
  <audio id="audioRemoto" autoplay></audio>
</div>
<script>
let contatoAtual = null;
let contatos = [];

async function carregarContatos() {
    const r = await fetch("/zap/contatos");
    contatos = await r.json();
    const lista = document.getElementById("listaContatos");
    if (contatos.length === 0) { lista.innerHTML = '<div class="vazio-contatos">Nenhum contato ainda. Toque em + para adicionar pelo ID.</div>'; return; }
    lista.innerHTML = "";
    contatos.forEach(c => {
        const div = document.createElement("div");
        div.className = "item-contato" + (contatoAtual === c.usuario ? " ativo" : "");
        div.onclick = () => abrirConversa(c);
        div.innerHTML = `<img src="${c.avatar}"><div><div class="nome">${c.usuario}</div><div class="idc">#${c.id_publico}</div></div>`;
        lista.appendChild(div);
    });
}

async function adicionarContato() {
    const id = prompt("Digite o ID (#) da pessoa que voce quer adicionar:");
    if (!id) return;
    const r = await fetch("/zap/adicionar_contato", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id: id.trim()}) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel adicionar."); return; }
    await carregarContatos();
}

function menuAdicionar() {
    const escolha = prompt("Digite:\\n1 - para adicionar um contato pelo ID\\n2 - para criar um grupo");
    if (escolha === "2") { window.location.href = "/zap/grupos"; }
    else { adicionarContato(); }
}

function abrirConversa(c) {
    contatoAtual = c.usuario;
    document.getElementById("semConversa").style.display = "none";
    document.getElementById("conversaAberta").style.display = "flex";
    document.getElementById("avatarChatAtual").src = c.avatar;
    document.getElementById("nomeChatAtual").textContent = c.usuario;
    document.getElementById("idChatAtual").textContent = "#" + c.id_publico;
    if (window.innerWidth <= 720) document.getElementById("sidebarZap").classList.add("recolhida");
    carregarContatos();
    carregarMensagens();
}

function renderizarBolha(m) {
    if (m.tipo === "sistema") return `<div class="bolha sistema">${m.conteudo}</div>`;
    let corpo = "";
    if (m.tipo === "texto") corpo = escaparHtml(m.conteudo);
    else if (m.tipo === "imagem") corpo = `<img src="${m.conteudo}">`;
    else if (m.tipo === "video") corpo = `<video src="${m.conteudo}" controls></video>`;
    else if (m.tipo === "audio") corpo = `<audio src="${m.conteudo}" controls></audio>`;
    const denunciar = m.tipo !== "sistema" && !m.minha ? `<span class="denunciar" onclick="denunciarMensagem(${m.id})">Denunciar</span>` : "";
    return `<div class="bolha ${m.minha ? 'minha' : 'dele'}">${corpo}${denunciar}</div>`;
}
function escaparHtml(t) { const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }

async function carregarMensagens() {
    if (!contatoAtual) return;
    const r = await fetch("/zap/mensagens/" + encodeURIComponent(contatoAtual));
    const d = await r.json();
    document.getElementById("badgeCripto").style.display = d.criptografado ? "inline-block" : "none";
    document.getElementById("botaoCriptografar").style.display = d.criptografado ? "none" : "inline-block";
    document.getElementById("botaoBloquear").textContent = d.bloqueado ? "Desbloquear" : "Bloquear";
    const caixa = document.getElementById("msgsZap");
    caixa.innerHTML = d.mensagens.map(renderizarBolha).join("");
    caixa.scrollTop = caixa.scrollHeight;
}

async function ativarCriptografia() {
    if (!contatoAtual) return;
    const frase = prompt("Digite a frase/senha de criptografia desta conversa (ex: uma data ou palavra que so voces dois sabem):");
    if (!frase) return;
    const r = await fetch("/zap/criptografar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({contato: contatoAtual, frase: frase}) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel ativar a criptografia."); return; }
    carregarMensagens();
}

async function alternarBloqueio() {
    if (!contatoAtual) return;
    const r = await fetch("/zap/bloquear", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({contato: contatoAtual}) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel."); return; }
    alert(d.bloqueado ? "Contato bloqueado." : "Contato desbloqueado.");
    carregarMensagens();
}

async function enviarTextoZap() {
    const campo = document.getElementById("campoZap");
    const texto = campo.value.trim();
    if (!texto || !contatoAtual) return;
    campo.value = "";
    const r = await fetch("/zap/enviar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({contato: contatoAtual, tipo: "texto", conteudo: texto}) });
    const d = await r.json();
    if (d.bloqueado) { window.location.href = "/zap"; return; }
    if (!d.ok) { alert(d.erro || "Nao foi possivel enviar."); }
    carregarMensagens();
}

async function enviarArquivo(input, tipo) {
    const arquivo = input.files[0];
    if (!arquivo || !contatoAtual) return;
    const form = new FormData();
    form.append("contato", contatoAtual); form.append("tipo", tipo); form.append("arquivo", arquivo);
    const r = await fetch("/zap/enviar_arquivo", { method: "POST", body: form });
    const d = await r.json();
    input.value = "";
    if (d.bloqueado) { window.location.href = "/zap"; return; }
    if (!d.ok) { alert(d.erro || "Nao foi possivel enviar."); }
    carregarMensagens();
}

async function denunciarMensagem(id) {
    if (!confirm("Denunciar esta mensagem para o bot do CHAT CPA analisar?")) return;
    await fetch("/zap/denunciar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mensagem_id: id}) });
    alert("Denuncia enviada.");
}

// ---- gravacao de audio ----
let gravador = null, pedacosAudio = [], gravandoAudio = false;
async function alternarGravacaoAudio() {
    const botao = document.getElementById("botaoGravar");
    if (gravandoAudio) { gravador.stop(); return; }
    if (!contatoAtual) { alert("Abra uma conversa primeiro."); return; }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        pedacosAudio = [];
        gravador = new MediaRecorder(stream);
        gravador.ondataavailable = e => pedacosAudio.push(e.data);
        gravador.onstop = async () => {
            gravandoAudio = false; botao.classList.remove("gravando");
            const blob = new Blob(pedacosAudio, { type: "audio/webm" });
            const form = new FormData();
            form.append("contato", contatoAtual); form.append("tipo", "audio"); form.append("arquivo", blob, "audio.webm");
            const r = await fetch("/zap/enviar_arquivo", { method: "POST", body: form });
            const d = await r.json();
            if (d.bloqueado) { window.location.href = "/zap"; return; }
            carregarMensagens();
            stream.getTracks().forEach(t => t.stop());
        };
        gravador.start(); gravandoAudio = true; botao.classList.add("gravando");
    } catch (e) { alert("Nao foi possivel acessar o microfone."); }
}

function voltarParaLista() {
    document.getElementById("sidebarZap").classList.remove("recolhida");
}

carregarContatos();
setInterval(() => { if (contatoAtual) carregarMensagens(); }, 4000);

// ---------- Ligacoes de voz (WebRTC + sinalizacao via polling) ----------
const CONFIG_ICE = { iceServers: """ + ICE_SERVERS_JSON + """ };
let pc = null, streamLocal = null, chamadaAtualId = null, souQuemLigou = false, contatoDaChamada = null;
let indiceCandidatosRecebidos = 0, pollCandidatos = null, pollStatusLigacao = null, pollChamadaEntrando = null;

let mutadoLocal = false;
let cameraLigada = false;
let chamadaComVideo = false;
function botoesEmChamadaHtml() {
    let html = '<button class="botao-chamada-circulo" id="botaoMudo" style="background:#333;color:#fff;" onclick="alternarMudo()">' + (mutadoLocal ? '&#128263;' : '&#127908;') + '</button>';
    if (chamadaComVideo) {
        html += '<button class="botao-chamada-circulo" id="botaoCamera" style="background:#333;color:#fff;" onclick="alternarCamera()">' + (cameraLigada ? '&#128249;' : '&#128683;') + '</button>';
    }
    html += '<button class="botao-chamada-circulo encerrar" onclick="encerrarChamada(true)">&#128222;</button>';
    return html;
}
function alternarMudo() {
    if (!streamLocal) return;
    mutadoLocal = !mutadoLocal;
    streamLocal.getAudioTracks().forEach(t => t.enabled = !mutadoLocal); // so o MEU audio, nao mexe no do outro lado
    const botao = document.getElementById("botaoMudo");
    if (botao) botao.innerHTML = mutadoLocal ? "&#128263;" : "&#127908;";
}
function alternarCamera() {
    if (!streamLocal) return;
    cameraLigada = !cameraLigada;
    streamLocal.getVideoTracks().forEach(t => t.enabled = cameraLigada);
    const botao = document.getElementById("botaoCamera");
    if (botao) botao.innerHTML = cameraLigada ? "&#128249;" : "&#128683;";
    document.getElementById("videoLocal").classList.toggle("ativo", cameraLigada);
}

function alternarTelasChamada() {
    const videoRemoto = document.getElementById("videoRemoto");
    const videoLocal = document.getElementById("videoLocal");
    // so faz sentido trocar se os dois videos estiverem ativos (chamada de video dos dois lados)
    if (!videoRemoto.classList.contains("ativo") || !videoLocal.classList.contains("ativo")) return;
    videoRemoto.classList.toggle("reduzido");
    videoLocal.classList.toggle("tela-cheia");
}

function abrirModalChamada(nome, avatar, statusTexto, botoesHtml, comVideo) {
    document.getElementById("nomeChamada").textContent = nome;
    document.getElementById("avatarChamada").src = avatar || (contatos.find(c => c.usuario === nome) || {}).avatar || "";
    document.getElementById("avatarChamada").style.display = comVideo ? "none" : "";
    document.getElementById("statusChamada").textContent = statusTexto;
    document.getElementById("botoesChamada").innerHTML = botoesHtml;
    document.getElementById("modalChamada").classList.add("aberto");
}
function fecharModalChamada() {
    document.getElementById("modalChamada").classList.remove("aberto");
    document.getElementById("avatarChamada").style.display = "";
    document.getElementById("videoRemoto").classList.remove("ativo", "reduzido");
    document.getElementById("videoRemoto").srcObject = null;
    document.getElementById("videoLocal").classList.remove("ativo", "tela-cheia");
    document.getElementById("videoLocal").srcObject = null;
    document.getElementById("avisoToqueChamada").style.display = "none";
    cameraLigada = false;
    chamadaComVideo = false;
}

async function criarConexao(alvoNome, comVideo) {
    pc = new RTCPeerConnection(CONFIG_ICE);
    streamLocal = await navigator.mediaDevices.getUserMedia({ audio: true, video: comVideo ? { facingMode: "user" } : false });
    streamLocal.getTracks().forEach(t => pc.addTrack(t, streamLocal));
    if (comVideo) {
        cameraLigada = true;
        const videoLocal = document.getElementById("videoLocal");
        videoLocal.srcObject = streamLocal;
        videoLocal.classList.add("ativo");
    }
    pc.ontrack = (ev) => {
        const audioRemoto = document.getElementById("audioRemoto");
        audioRemoto.srcObject = ev.streams[0];
        audioRemoto.play().catch(() => mostrarAvisoToqueParaOuvir());
        const videoRemoto = document.getElementById("videoRemoto");
        if (ev.track.kind === "video") {
            videoRemoto.srcObject = ev.streams[0];
            videoRemoto.classList.add("ativo");
            // Alguns navegadores bloqueiam o autoplay de video com som se nao
            // houver um gesto recente do usuario - sem isso a pessoa via a tela
            // preta/sem imagem mesmo com a chamada conectada. Tentamos tocar e,
            // se for bloqueado, mostramos um botao para o usuario liberar.
            videoRemoto.play().catch(() => mostrarAvisoToqueParaOuvir());
        }
    };
    pc.onicecandidate = (ev) => {
        if (ev.candidate && chamadaAtualId) {
            fetch("/zap/chamada/candidato", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({chamada_id: chamadaAtualId, candidato: ev.candidate}) });
        }
    };
    pc.onconnectionstatechange = () => {
        if (!pc) return;
        if (pc.connectionState === "connected") {
            const statusEl = document.getElementById("statusChamada");
            if (statusEl) statusEl.textContent = "Em chamada";
        } else if (pc.connectionState === "failed") {
            // Falha de conexao geralmente e falta de um servidor TURN quando as
            // duas pessoas estao atras de redes fechadas (dados moveis, etc).
            const statusEl = document.getElementById("statusChamada");
            if (statusEl) statusEl.textContent = "Nao foi possivel conectar (rede). Tente por Wi-Fi.";
            setTimeout(() => encerrarChamada(true), 2500);
        } else if (pc.connectionState === "disconnected" || pc.connectionState === "closed") {
            encerrarChamada(false);
        }
    };
}

function mostrarAvisoToqueParaOuvir() {
    const aviso = document.getElementById("avisoToqueChamada");
    if (aviso) aviso.style.display = "block";
}
function liberarMidiaChamada() {
    const videoRemoto = document.getElementById("videoRemoto");
    const audioRemoto = document.getElementById("audioRemoto");
    videoRemoto.muted = false;
    videoRemoto.play().catch(() => {});
    audioRemoto.play().catch(() => {});
    const aviso = document.getElementById("avisoToqueChamada");
    if (aviso) aviso.style.display = "none";
}

function iniciarPollCandidatos() {
    indiceCandidatosRecebidos = 0;
    // Candidatos ICE podem chegar do outro lado antes da nossa descricao remota
    // estar pronta (setRemoteDescription so acontece quando a resposta chega).
    // Tentar adicionar um candidato antes disso lanca erro e, se descartarmos
    // esse candidato, a chamada as vezes conecta so em uma direcao (ou nao
    // conecta) - por isso guardamos numa fila e tentamos de novo depois.
    let filaCandidatosPendentes = [];
    async function tentarAdicionar(candidato) {
        if (pc && pc.remoteDescription && pc.remoteDescription.type) {
            try { await pc.addIceCandidate(candidato); } catch (e) {}
        } else {
            filaCandidatosPendentes.push(candidato);
        }
    }
    pollCandidatos = setInterval(async () => {
        if (!chamadaAtualId || !pc) return;
        if (pc.remoteDescription && pc.remoteDescription.type && filaCandidatosPendentes.length) {
            const pendentes = filaCandidatosPendentes;
            filaCandidatosPendentes = [];
            for (const c of pendentes) { try { await pc.addIceCandidate(c); } catch (e) {} }
        }
        const r = await fetch("/zap/chamada/candidatos/" + chamadaAtualId + "?desde=" + indiceCandidatosRecebidos);
        const d = await r.json();
        for (const c of d.candidatos) { await tentarAdicionar(c); }
        indiceCandidatosRecebidos += d.candidatos.length;
        if (d.status === "encerrada" || d.status === "recusada") encerrarChamada(false);
    }, 1500);
}

async function iniciarChamada(comVideo) {
    if (!contatoAtual) return;
    contatoDaChamada = contatoAtual;
    souQuemLigou = true;
    chamadaComVideo = !!comVideo;
    abrirModalChamada(contatoDaChamada, null, "Chamando...", '<button class="botao-chamada-circulo encerrar" onclick="encerrarChamada(true)">&#128222;</button>', chamadaComVideo);
    try {
        await criarConexao(contatoDaChamada, chamadaComVideo);
    } catch (e) {
        alert(chamadaComVideo ? "Nao foi possivel acessar a camera/microfone." : "Nao foi possivel acessar o microfone.");
        fecharModalChamada();
        return;
    }
    const oferta = await pc.createOffer();
    await pc.setLocalDescription(oferta);
    const r = await fetch("/zap/chamada/iniciar", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({contato: contatoDaChamada, oferta}) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel ligar."); fecharModalChamada(); return; }
    chamadaAtualId = d.chamada_id;
    iniciarPollCandidatos();
    pollStatusLigacao = setInterval(async () => {
        const rs = await fetch("/zap/chamada/status/" + chamadaAtualId);
        const ds = await rs.json();
        if (ds.status === "aceita" && ds.resposta && pc && !pc.currentRemoteDescription) {
            await pc.setRemoteDescription(ds.resposta);
            document.getElementById("statusChamada").textContent = "Em chamada";
            document.getElementById("botoesChamada").innerHTML = botoesEmChamadaHtml();
        } else if (ds.status === "recusada") {
            document.getElementById("statusChamada").textContent = "Chamada recusada";
            setTimeout(() => encerrarChamada(false), 1200);
        } else if (ds.status === "encerrada") {
            encerrarChamada(false);
        }
    }, 1500);
}

async function verificarChamadaEntrando() {
    if (chamadaAtualId) return; // ja em uma chamada
    const r = await fetch("/zap/chamada/pendente");
    const d = await r.json();
    if (!d.chamada) return;
    chamadaAtualId = d.chamada.id;
    contatoDaChamada = d.chamada.de;
    souQuemLigou = false;
    window._ofertaRecebida = d.chamada.oferta;
    chamadaComVideo = !!(d.chamada.oferta && d.chamada.oferta.sdp && d.chamada.oferta.sdp.indexOf("m=video") !== -1);
    abrirModalChamada(contatoDaChamada, null, chamadaComVideo ? "Chamada de video recebida..." : "Chamada recebida...",
        '<button class="botao-chamada-circulo aceitar" onclick="aceitarChamada()">&#9742;</button>' +
        '<button class="botao-chamada-circulo recusar" onclick="recusarChamada()">&#10006;</button>', chamadaComVideo);
}

async function aceitarChamada() {
    try {
        await criarConexao(contatoDaChamada, chamadaComVideo);
    } catch (e) {
        alert(chamadaComVideo ? "Nao foi possivel acessar a camera/microfone." : "Nao foi possivel acessar o microfone.");
        recusarChamada();
        return;
    }
    await pc.setRemoteDescription(window._ofertaRecebida);
    const resposta = await pc.createAnswer();
    await pc.setLocalDescription(resposta);
    await fetch("/zap/chamada/responder", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({chamada_id: chamadaAtualId, resposta, aceitar: true}) });
    document.getElementById("statusChamada").textContent = "Em chamada";
    document.getElementById("botoesChamada").innerHTML = botoesEmChamadaHtml();
    iniciarPollCandidatos();
}

async function recusarChamada() {
    await fetch("/zap/chamada/responder", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({chamada_id: chamadaAtualId, aceitar: false}) });
    encerrarChamada(false);
}

async function encerrarChamada(avisarServidor) {
    if (avisarServidor && chamadaAtualId) {
        fetch("/zap/chamada/encerrar", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({chamada_id: chamadaAtualId}) });
    }
    if (pc) { pc.close(); pc = null; }
    if (streamLocal) { streamLocal.getTracks().forEach(t => t.stop()); streamLocal = null; }
    if (pollCandidatos) { clearInterval(pollCandidatos); pollCandidatos = null; }
    if (pollStatusLigacao) { clearInterval(pollStatusLigacao); pollStatusLigacao = null; }
    chamadaAtualId = null; contatoDaChamada = null; mutadoLocal = false;
    fecharModalChamada();
}

setInterval(verificarChamadaEntrando, 2500);
</script>
</body></html>
"""

# ---------- GRUPOS DO ZAP ----------
PAGINA_ZAP_GRUPOS = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Grupos - ZAP</title>
<style>
""" + ESTILO_COMUM + """
.topo { padding:16px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; gap:12px; font-weight:bold; }
.topo a { color:#fff; text-decoration:none; font-size:18px; }
.container { padding:16px; max-width:520px; margin:0 auto; }
.caixa-grupo { background:#0d0d0d; border:1px solid #ffffff22; border-radius:12px; padding:14px; margin-bottom:18px; }
.caixa-grupo input[type=text] { width:100%; padding:10px; border-radius:8px; border:1px solid #ffffff22; background:#000; color:#f2f2f2; margin-top:8px; }
.lista-membros-check { display:flex; flex-direction:column; gap:6px; margin-top:8px; max-height:160px; overflow-y:auto; }
.lista-membros-check label { display:flex; align-items:center; gap:8px; font-size:13px; }
.caixa-grupo button { margin-top:10px; padding:10px 16px; border-radius:8px; border:none; background:#ffffff; color:#000; font-weight:bold; cursor:pointer; }
.item-grupo { display:flex; align-items:center; gap:10px; padding:12px; background:#0d0d0d; border:1px solid #ffffff22; border-radius:10px; margin-bottom:8px; cursor:pointer; }
.item-grupo img { width:38px; height:38px; border-radius:50%; object-fit:cover; background:#1a1a1a; }
</style></head>
<body>
<div class="topo"><a href="/zap">&#8592;</a>Grupos do ZAP</div>
<div class="container">
  <div class="caixa-grupo">
    <b>Criar grupo</b>
    <input type="text" id="nomeGrupo" placeholder="Nome do grupo">
    <div class="lista-membros-check" id="listaMembros">Carregando contatos...</div>
    <button onclick="criarGrupo()">Criar grupo</button>
  </div>
  <div id="listaGrupos"></div>
</div>
<script>
async function carregarContatosParaGrupo() {
    const r = await fetch("/zap/contatos");
    const contatos = await r.json();
    const div = document.getElementById("listaMembros");
    if (!contatos.length) { div.textContent = "Adicione contatos primeiro para poder criar um grupo."; return; }
    div.innerHTML = contatos.map(c => `<label><input type="checkbox" value="${c.usuario}"> ${c.usuario}</label>`).join("");
}
async function criarGrupo() {
    const nome = document.getElementById("nomeGrupo").value.trim();
    if (!nome) { alert("Digite um nome para o grupo."); return; }
    const membros = Array.from(document.querySelectorAll("#listaMembros input:checked")).map(i => i.value);
    const r = await fetch("/zap/grupos/criar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({nome, membros}) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel criar."); return; }
    window.location.href = "/zap/grupo/" + d.grupo_id;
}
async function carregarGrupos() {
    const r = await fetch("/zap/grupos/lista");
    const grupos = await r.json();
    const div = document.getElementById("listaGrupos");
    div.innerHTML = grupos.map(g => `<div class="item-grupo" onclick="window.location.href='/zap/grupo/${g.id}'"><img src="${g.foto || ''}"><div>${g.nome}${g.verificado ? ' &#9989;' : ''}</div></div>`).join("") || "<div style='color:#777;'>Voce ainda nao tem grupos.</div>";
}
carregarContatosParaGrupo();
carregarGrupos();
</script>
</body></html>
"""

PAGINA_ZAP_GRUPO_CHAT = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>{nome_grupo} - ZAP</title>
<style>
""" + ESTILO_COMUM + """
body { display:flex; flex-direction:column; height:100vh; }
.topo-chat { padding:14px 18px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; gap:12px; }
.topo-chat a { color:#fff; text-decoration:none; font-size:18px; }
.msgs-zap { flex:1; overflow-y:auto; padding:18px; display:flex; flex-direction:column; gap:10px; }
.bolha { max-width:70%; padding:10px 14px; border-radius:12px; line-height:1.4; font-size:14px; }
.bolha.minha { align-self:flex-end; background:#3ddc6a33; border:1px solid #3ddc6a55; }
.bolha.dele { align-self:flex-start; background:#0d0d0d; border:1px solid #ffffff22; }
.bolha .remetente { font-size:11px; color:#888; margin-bottom:2px; }
.area-input-zap { padding:12px 16px calc(12px + env(safe-area-inset-bottom)); border-top:1px solid #ffffff22; display:flex; gap:8px; }
.area-input-zap input[type=text] { flex:1; padding:12px 14px; border-radius:20px; border:1px solid #ffffff33; background:#0d0d0d; color:#f2f2f2; }
.area-input-zap button { background:#fff; color:#000; border:none; border-radius:50%; width:40px; height:40px; cursor:pointer; }
.botao-info-grupo { margin-left:auto; background:none; border:none; color:#fff; font-size:18px; cursor:pointer; }
.folha-grupo { display:none; position:fixed; inset:0; background:#00000099; z-index:120; align-items:flex-end; justify-content:center; }
.folha-grupo.aberta { display:flex; }
.folha-grupo-conteudo { width:100%; max-width:480px; max-height:82vh; background:#0d0d0d; border-radius:16px 16px 0 0; border:1px solid #ffffff22; border-bottom:none; display:flex; flex-direction:column; overflow-y:auto; padding:16px; }
.folha-grupo-topo { display:flex; align-items:center; justify-content:space-between; font-weight:bold; margin-bottom:12px; }
.folha-grupo-topo span { cursor:pointer; color:#888; font-size:20px; }
.info-grupo-foto { display:flex; flex-direction:column; align-items:center; gap:8px; margin-bottom:14px; }
.info-grupo-foto img { width:76px; height:76px; border-radius:50%; object-fit:cover; background:#1a1a1a; }
.info-grupo-foto label { font-size:12px; color:#888; cursor:pointer; text-decoration:underline; }
.info-grupo-nome { display:flex; gap:8px; margin-bottom:16px; }
.info-grupo-nome input { flex:1; padding:10px; border-radius:8px; border:1px solid #ffffff22; background:#000; color:#f2f2f2; }
.info-grupo-nome button { padding:10px 14px; border-radius:8px; border:none; background:#fff; color:#000; font-weight:bold; cursor:pointer; }
.lista-membros-grupo { font-size:13px; }
.linha-membro-grupo { display:flex; align-items:center; gap:8px; padding:8px 0; border-bottom:1px solid #ffffff14; }
.linha-membro-grupo img { width:30px; height:30px; border-radius:50%; object-fit:cover; background:#1a1a1a; }
.linha-membro-grupo .nome-membro { flex:1; }
.linha-membro-grupo .tag-admin-membro { font-size:10px; color:#3ddc6a; margin-left:6px; }
.linha-membro-grupo button { background:#1a1a1a; border:1px solid #ffffff22; color:#f2f2f2; font-size:11px; padding:5px 8px; border-radius:6px; cursor:pointer; margin-left:4px; }
.adicionar-membro-grupo { display:flex; gap:8px; margin:12px 0; }
.adicionar-membro-grupo input { flex:1; padding:9px; border-radius:8px; border:1px solid #ffffff22; background:#000; color:#f2f2f2; font-size:13px; }
.adicionar-membro-grupo button { padding:9px 12px; border-radius:8px; border:none; background:#fff; color:#000; font-weight:bold; cursor:pointer; font-size:12px; }
.botao-sair-grupo { margin-top:14px; width:100%; padding:11px; border-radius:8px; border:1px solid #ff3b5c55; background:none; color:#ff3b5c; font-weight:bold; cursor:pointer; }
</style></head>
<body>
<div class="topo-chat"><a href="/zap/grupos">&#8592;</a><b>{nome_grupo}</b>{selo_dev_grupo}<button class="botao-info-grupo" onclick="abrirInfoGrupo()">&#9881;</button></div>
<div class="msgs-zap" id="msgsGrupo"></div>
<div class="area-input-zap">
  <input type="text" id="campoGrupo" placeholder="Mensagem" onkeydown="if(event.key==='Enter')enviarMsgGrupo()">
  <button onclick="enviarMsgGrupo()">&#10148;</button>
</div>
<div class="folha-grupo" id="folhaGrupo" onclick="fecharInfoGrupo()">
  <div class="folha-grupo-conteudo" onclick="event.stopPropagation()">
    <div class="folha-grupo-topo"><b>Informacoes do grupo</b><span onclick="fecharInfoGrupo()">&times;</span></div>
    <div class="info-grupo-foto">
      <img id="fotoGrupoAtual" src="">
      <label id="rotuloFotoGrupo" style="display:none;">Trocar foto<input type="file" id="arquivoFotoGrupo" accept="image/*" style="display:none;" onchange="salvarInfoGrupo()"></label>
    </div>
    <div class="info-grupo-nome">
      <input type="text" id="nomeGrupoInput" placeholder="Nome do grupo">
      <button id="botaoSalvarNomeGrupo" style="display:none;" onclick="salvarInfoGrupo()">Salvar</button>
    </div>
    <div class="adicionar-membro-grupo" id="blocoAdicionarMembro" style="display:none;">
      <input type="text" id="campoAdicionarMembro" placeholder="Apelido do contato">
      <button onclick="adicionarMembroGrupo()">Adicionar</button>
    </div>
    <div class="lista-membros-grupo" id="listaMembrosGrupo"></div>
    <button class="botao-sair-grupo" onclick="sairDoGrupo()">Sair do grupo</button>
  </div>
</div>
<script>
const grupoId = {grupo_id};
let souAdminDoGrupo = false;
function escaparHtml(t) { const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
async function abrirInfoGrupo() {
    document.getElementById("folhaGrupo").classList.add("aberta");
    const r = await fetch("/zap/grupo/" + grupoId + "/info");
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel carregar as informacoes do grupo."); return; }
    souAdminDoGrupo = d.sou_admin;
    document.getElementById("fotoGrupoAtual").src = d.foto || "";
    document.getElementById("nomeGrupoInput").value = d.nome;
    document.getElementById("nomeGrupoInput").disabled = !souAdminDoGrupo;
    document.getElementById("botaoSalvarNomeGrupo").style.display = souAdminDoGrupo ? "" : "none";
    document.getElementById("rotuloFotoGrupo").style.display = souAdminDoGrupo ? "" : "none";
    document.getElementById("blocoAdicionarMembro").style.display = souAdminDoGrupo ? "flex" : "none";
    const lista = document.getElementById("listaMembrosGrupo");
    lista.innerHTML = d.membros.map(m => {
        let botoes = "";
        if (souAdminDoGrupo) {
            botoes += `<button onclick="promoverMembroGrupo('${m.usuario}')">${m.admin ? "Remover admin" : "Tornar admin"}</button>`;
            botoes += `<button onclick="removerMembroGrupo('${m.usuario}')">Remover</button>`;
        }
        return `<div class="linha-membro-grupo"><img src="${m.foto || ''}"><span class="nome-membro">${escaparHtml(m.usuario)}${m.admin ? '<span class="tag-admin-membro">ADMIN</span>' : ''}</span>${botoes}</div>`;
    }).join("");
}
function fecharInfoGrupo() { document.getElementById("folhaGrupo").classList.remove("aberta"); }
async function salvarInfoGrupo() {
    const form = new FormData();
    const nome = document.getElementById("nomeGrupoInput").value.trim();
    const arquivo = document.getElementById("arquivoFotoGrupo").files[0];
    if (nome) form.append("nome", nome);
    if (arquivo) form.append("foto", arquivo);
    const r = await fetch("/zap/grupo/" + grupoId + "/editar", { method: "POST", body: form });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel salvar."); return; }
    document.querySelector(".topo-chat b").textContent = d.nome;
    abrirInfoGrupo();
}
async function adicionarMembroGrupo() {
    const campo = document.getElementById("campoAdicionarMembro");
    const usuario = campo.value.trim();
    if (!usuario) return;
    const r = await fetch("/zap/grupo/" + grupoId + "/membro/adicionar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({usuario}) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel adicionar."); return; }
    campo.value = "";
    abrirInfoGrupo();
}
async function removerMembroGrupo(usuario) {
    if (!confirm("Remover " + usuario + " do grupo?")) return;
    const r = await fetch("/zap/grupo/" + grupoId + "/membro/remover", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({usuario}) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel remover."); return; }
    abrirInfoGrupo();
}
async function promoverMembroGrupo(usuario) {
    const r = await fetch("/zap/grupo/" + grupoId + "/membro/promover", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({usuario}) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel alterar o admin."); return; }
    abrirInfoGrupo();
}
async function sairDoGrupo() {
    if (!confirm("Tem certeza que quer sair deste grupo?")) return;
    const r = await fetch("/zap/grupo/" + grupoId + "/sair", { method: "POST" });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || "Nao foi possivel sair do grupo."); return; }
    window.location.href = "/zap/grupos";
}
async function carregarMsgsGrupo() {
    const r = await fetch("/zap/grupo/" + grupoId + "/mensagens");
    const d = await r.json();
    const caixa = document.getElementById("msgsGrupo");
    caixa.innerHTML = d.mensagens.map(m => `<div class="bolha ${m.minha ? 'minha' : 'dele'}">${!m.minha ? '<div class=\\"remetente\\">' + m.remetente + '</div>' : ''}${escaparHtml(m.conteudo)}</div>`).join("");
    caixa.scrollTop = caixa.scrollHeight;
}
async function enviarMsgGrupo() {
    const campo = document.getElementById("campoGrupo");
    const texto = campo.value.trim();
    if (!texto) return;
    campo.value = "";
    await fetch("/zap/grupo/" + grupoId + "/enviar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({texto}) });
    carregarMsgsGrupo();
}
carregarMsgsGrupo();
setInterval(carregarMsgsGrupo, 4000);
</script>
</body></html>
"""

# ---------- CPA CODES (chat focado em gerar codigo) ----------
PAGINA_EXTENSAO = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CPA Codes</title>
<style>
""" + ESTILO_COMUM + """
body { display:flex; flex-direction:column; height:100vh; overflow:hidden; }
.topo-ext { padding:14px 18px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; gap:12px; }
.topo-ext a { color:#fff; text-decoration:none; font-size:18px; }
.seletor-linguagem { margin-left:auto; background:#0d0d0d; color:#fff; border:1px solid #ffffff33; border-radius:6px; padding:6px; font-size:12px; }
.msgs-ext { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:16px; }
.bloco-ext { max-width:80%; padding:12px 16px; border-radius:12px; line-height:1.5; font-size:14px; }
.bloco-ext.usuario { align-self:flex-end; background:#1a1a1a; }
.bloco-ext.jarvis { align-self:flex-start; background:#0d0d0d; border:1px solid #ffffff22; }
.bloco-ext pre { background:#000; border:1px solid #ffffff22; border-radius:8px; padding:12px; overflow-x:auto; position:relative; font-family: 'Consolas', monospace; font-size:13px; }
.bloco-ext .copiar { position:absolute; top:6px; right:8px; background:#1a1a1a; border:1px solid #ffffff33; color:#fff; font-size:11px; padding:3px 8px; border-radius:6px; cursor:pointer; }
.area-input-ext { padding:16px; border-top:1px solid #ffffff22; display:flex; gap:8px; }
.area-input-ext input { flex:1; padding:14px; border-radius:8px; border:1px solid #ffffff33; background:#0d0d0d; color:#f2f2f2; font-size:14px; }
.area-input-ext button { padding:14px 18px; border-radius:8px; border:none; background:#ffffff; color:#000; font-weight:bold; cursor:pointer; }
</style></head>
<body>
<div class="topo-ext"><a href="/inicio">&#8592;</a><b>CPA Codes</b>
  <select class="seletor-linguagem" id="linguagem">
    <option value="qualquer linguagem apropriada">Auto</option>
    <option value="Python">Python</option>
    <option value="Java">Java</option>
    <option value="JavaScript">JavaScript</option>
    <option value="C#">C#</option>
    <option value="HTML/CSS/JS">HTML/CSS/JS</option>
  </select>
</div>
<div class="msgs-ext" id="msgsExt">
  <div class="bloco-ext jarvis">Modo Extensao ligado. Descreva o script/codigo que voce precisa (ex: "cria um script em Python que renomeia arquivos de uma pasta").</div>
</div>
<div class="area-input-ext">
  <input type="text" id="campoExt" placeholder="Descreva o codigo que voce quer..." onkeydown="if(event.key==='Enter')enviarExt()">
  <button onclick="enviarExt()">Gerar</button>
</div>
<script>
function formatarResposta(texto) {
    const partes = texto.split(/```(\\w*)\\n?/);
    let html = "";
    for (let i = 0; i < partes.length; i++) {
        if (i % 2 === 0) { html += escaparHtml(partes[i]).replace(/\\n/g, "<br>"); }
        else {
            const codigo = partes[++i] || "";
            html += `<pre><span class="copiar" onclick="copiarCodigo(this)">Copiar</span><code>${escaparHtml(codigo)}</code></pre>`;
        }
    }
    return html;
}
function escaparHtml(t) { const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
function copiarCodigo(botao) {
    const codigo = botao.nextElementSibling.textContent;
    navigator.clipboard.writeText(codigo);
    botao.textContent = "Copiado!";
    setTimeout(() => botao.textContent = "Copiar", 1200);
}
async function enviarExt() {
    const campo = document.getElementById("campoExt");
    const texto = campo.value.trim();
    if (!texto) return;
    const caixa = document.getElementById("msgsExt");
    caixa.innerHTML += `<div class="bloco-ext usuario">${escaparHtml(texto)}</div>`;
    campo.value = "";
    caixa.scrollTop = caixa.scrollHeight;
    const linguagem = document.getElementById("linguagem").value;
    const r = await fetch("/extensao/chat", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mensagem: texto, linguagem: linguagem}) });
    const d = await r.json();
    caixa.innerHTML += `<div class="bloco-ext jarvis">${formatarResposta(d.resposta || "Sem resposta.")}</div>`;
    caixa.scrollTop = caixa.scrollHeight;
}
</script>
</body></html>
"""


def pagina_login():
    if GOOGLE_CLIENT_ID:
        bloco_google = f"""
        <script src="https://accounts.google.com/gsi/client" async defer></script>
        <div class="bloco-google">
          <div id="g_id_onload" data-client_id="{GOOGLE_CLIENT_ID}" data-callback="aoLoginGoogle" data-auto_prompt="false"></div>
          <div class="g_id_signin" data-type="standard" data-shape="pill" data-theme="filled_black" data-text="continue_with" data-size="large" data-logo_alignment="left"></div>
        </div>
        <div class="divisor">ou</div>
        """
    else:
        bloco_google = ""
    return PAGINA_LOGIN.replace("{bloco_google}", bloco_google).replace("{logo_url}", obter_config("logo_login", "/static/logo.jpg"))


@app.route("/", methods=["GET"])
def login():
    if session.get("usuario"):
        return redirect(url_for("inicio"))
    return pagina_login()


@app.route("/auth/enviar_codigo", methods=["POST"])
def auth_enviar_codigo():
    dados = request.get_json() or {}
    email = (dados.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"ok": False, "erro": "Digite um email valido."})
    codigo = gerar_codigo()
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO codigos_verificacao (email, codigo, criado_em) VALUES (?, ?, ?) "
        "ON CONFLICT(email) DO UPDATE SET codigo = excluded.codigo, criado_em = excluded.criado_em",
        (email, codigo, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    if not email_esta_configurado():
        # Nem Resend nem SMTP configurados: nem tenta mandar, ja responde com o
        # codigo de teste na hora para a pessoa nao ficar travada na tela de login.
        print(f"[CHAT CPA] (email nao configurado) codigo para {email}: {codigo}")
        return jsonify({"ok": True, "codigo_teste": codigo})
    # Dispara o envio numa thread, mas espera alguns segundos por ela: se o SMTP
    # falhar rapido (senha errada, porta bloqueada pelo host, etc.) a pessoa fica
    # sabendo na hora em vez de ficar esperando um email que nunca vai chegar.
    # Se so estiver lento (mais comum), a resposta segue positiva e o envio
    # continua rodando em segundo plano.
    resultado_caixa = {}

    def _enviar_e_guardar():
        resultado_caixa["ok"] = enviar_email_codigo(email, codigo)

    thread_envio = threading.Thread(target=_enviar_e_guardar, daemon=True)
    thread_envio.start()
    thread_envio.join(timeout=6)
    if "ok" in resultado_caixa and resultado_caixa["ok"] is False:
        return jsonify({"ok": False, "erro": "Nao foi possivel enviar o codigo por email agora. Tente de novo em instantes."})
    return jsonify({"ok": True})


@app.route("/auth/verificar_codigo", methods=["POST"])
def auth_verificar_codigo():
    dados = request.get_json() or {}
    email = (dados.get("email") or "").strip().lower()
    codigo = (dados.get("codigo") or "").strip()
    if not email or not codigo:
        return jsonify({"ok": False, "erro": "Dados incompletos."})
    conexao = obter_bd()
    linha_codigo = conexao.execute("SELECT * FROM codigos_verificacao WHERE email = ?", (email,)).fetchone()
    if not linha_codigo or linha_codigo["codigo"] != codigo:
        conexao.close()
        return jsonify({"ok": False, "erro": "Codigo incorreto."})
    criado_em = datetime.fromisoformat(linha_codigo["criado_em"])
    if datetime.now() - criado_em > timedelta(minutes=MINUTOS_VALIDADE_CODIGO):
        conexao.close()
        return jsonify({"ok": False, "erro": "Codigo expirado. Peça um novo."})
    conexao.execute("DELETE FROM codigos_verificacao WHERE email = ?", (email,))
    conexao.commit()
    linha_usuario = conexao.execute("SELECT * FROM usuarios WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    if linha_usuario:
        conexao.close()
        session["usuario"] = linha_usuario["usuario"]
        return jsonify({"ok": True, "precisa_cadastro": False})
    id_sugerido = gerar_id_publico(conexao) if email != EMAIL_DONO.lower() else 1
    conexao.close()
    session["email_verificado"] = email
    session["email_verificado_em"] = datetime.now().isoformat()
    return jsonify({"ok": True, "precisa_cadastro": True, "id_sugerido": id_sugerido})


@app.route("/auth/completar_cadastro", methods=["POST"])
def auth_completar_cadastro():
    email = session.get("email_verificado")
    horario_verificado = session.get("email_verificado_em")
    if not email or not horario_verificado:
        return jsonify({"ok": False, "erro": "Verifique seu email novamente."})
    if datetime.now() - datetime.fromisoformat(horario_verificado) > timedelta(minutes=MINUTOS_VALIDADE_CODIGO):
        session.pop("email_verificado", None)
        session.pop("email_verificado_em", None)
        return jsonify({"ok": False, "erro": "Sessao expirada. Verifique seu email novamente."})
    apelido = request.form.get("apelido", "").strip()
    data_nascimento = request.form.get("data_nascimento", "").strip() or None
    if not apelido:
        return jsonify({"ok": False, "erro": "Escolha um apelido."})
    conexao = obter_bd()
    existente = conexao.execute("SELECT 1 FROM usuarios WHERE usuario = ? COLLATE NOCASE", (apelido,)).fetchone()
    if existente:
        conexao.close()
        return jsonify({"ok": False, "erro": "Esse apelido ja esta em uso."})
    eh_dono = email == EMAIL_DONO.lower()
    try:
        id_publico = int(request.form.get("id_publico"))
    except (TypeError, ValueError):
        id_publico = None
    if id_publico is None or (id_publico == 1 and not eh_dono) or (id_publico != 1 and id_publico <= 11):
        id_publico = 1 if eh_dono else gerar_id_publico(conexao)
    else:
        em_uso = conexao.execute("SELECT 1 FROM usuarios WHERE id_publico = ?", (id_publico,)).fetchone()
        if em_uso:
            id_publico = 1 if eh_dono else gerar_id_publico(conexao)
    conexao.close()
    foto_perfil = salvar_imagem(request.files.get("foto_perfil"))
    criar_usuario(apelido, email, foto_perfil=foto_perfil, data_nascimento=data_nascimento, id_publico=id_publico)
    session.pop("email_verificado", None)
    session.pop("email_verificado_em", None)
    session["usuario"] = apelido
    return jsonify({"ok": True})


@app.route("/auth/google", methods=["POST"])
def auth_google():
    dados = request.get_json() or {}
    token = dados.get("credential", "").strip()
    if not token:
        return jsonify({"ok": False, "erro": "Token ausente."}), 400
    try:
        with urllib.request.urlopen(
            "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(token), timeout=8
        ) as resposta_http:
            info = json.loads(resposta_http.read().decode())
    except Exception:
        return jsonify({"ok": False, "erro": "Nao foi possivel validar o login do Google."}), 400
    if GOOGLE_CLIENT_ID and info.get("aud") != GOOGLE_CLIENT_ID:
        return jsonify({"ok": False, "erro": "Token nao pertence a este site."}), 400
    email = (info.get("email") or "").strip()
    if not email or str(info.get("email_verified")).lower() != "true":
        return jsonify({"ok": False, "erro": "Email do Google nao verificado."}), 400
    nome = (info.get("name") or email.split("@")[0]).strip()
    foto = info.get("picture") or None
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM usuarios WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    if linha:
        session["usuario"] = linha["usuario"]
        conexao.close()
        return jsonify({"ok": True})
    usuario_base = "".join(nome.split()) or email.split("@")[0]
    usuario_final = usuario_base
    contador = 1
    while conexao.execute("SELECT 1 FROM usuarios WHERE usuario = ? COLLATE NOCASE", (usuario_final,)).fetchone():
        contador += 1
        usuario_final = f"{usuario_base}{contador}"
    conexao.close()
    criar_usuario(usuario_final, email, foto_perfil=foto)
    session["usuario"] = usuario_final
    return jsonify({"ok": True})


@app.route("/carregando")
def carregando():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    usuario = session["usuario"]
    linha = buscar_usuario(usuario)
    if linha and not linha["viu_boas_vindas"]:
        conexao = obter_bd()
        conexao.execute("UPDATE usuarios SET viu_boas_vindas = 1 WHERE usuario = ? COLLATE NOCASE", (usuario,))
        conexao.commit()
        conexao.close()
    # A tela cinematografica de boas-vindas foi trocada pela tela de carregamento
    # preta padrao (mais leve e consistente) tambem na primeira vez que a pessoa entra.
    return PAGINA_CARREGANDO


@app.route("/inicio")
def inicio():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    marcar_atividade(session["usuario"])
    pagina = PAGINA_INICIO.replace("{fundo_url}", obter_config("fundo_inicio", FUNDO_INICIO_URL))
    pagina = pagina.replace("{qtd_online}", str(contar_online()))
    pagina = pagina.replace("{qtd_contas}", str(contar_contas()))

    def icone_img(chave, letra, padrao=None):
        url = obter_config(chave) or padrao
        return f'<img src="{url}">' if url else letra

    pagina = pagina.replace("{icone_jarvisweb}", icone_img("icone_jarvisweb", "S"))
    pagina = pagina.replace("{icone_jarvis}", icone_img("icone_jarvis", "C", "/static/logo.jpg"))
    pagina = pagina.replace("{icone_zap}", icone_img("icone_zap", "Z"))
    pagina = pagina.replace("{icone_suporte}", icone_img("icone_suporte", "S"))
    pagina = pagina.replace("{icone_app_url}", obter_config("icone_app", "/static/logo.jpg"))
    return pagina


@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    marcar_atividade(session["usuario"])
    return jsonify({"ok": True, "online": contar_online(), "contas": contar_contas()})


@app.route("/favicon.ico")
def favicon():
    icone = obter_config("icone_app") or obter_config("icone_jarvis") or obter_config("logo_login")
    if icone:
        return redirect(icone)
    return "", 204


@app.route("/manifest.json")
def manifest_json():
    icone = obter_config("icone_app", "/static/logo.jpg")
    manifest = {
        "name": "CHAT CPA",
        "short_name": "CHAT CPA",
        "start_url": "/inicio",
        "scope": "/",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": [
            {"src": icone, "sizes": "192x192", "type": "image/png"},
            {"src": icone, "sizes": "512x512", "type": "image/png"},
        ],
    }
    return jsonify(manifest)


@app.route("/service-worker.js")
def service_worker():
    conteudo = (
        "self.addEventListener('install', e => self.skipWaiting());\n"
        "self.addEventListener('activate', e => self.clients.claim());\n"
        "self.addEventListener('fetch', e => {});\n"
    )
    return app.response_class(conteudo, mimetype="application/javascript")


PAGINA_BAIXAR = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Baixar o CHAT CPA</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#000000">
<link rel="apple-touch-icon" href="{icone_app_url}">
<style>
""" + ESTILO_COMUM + """
body { min-height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:30px; text-align:center; }
img.icone-grande { width:88px; height:88px; border-radius:22px; margin-bottom:18px; box-shadow:0 0 24px #ffffff22; object-fit:cover; }
h2 { margin:0 0 8px; }
p { color:#999; font-size:14px; max-width:320px; line-height:1.5; }
button.principal { margin-top:20px; padding:14px 26px; border-radius:12px; border:none; background:#ffffff; color:#000; font-weight:bold; cursor:pointer; font-size:15px; }
.passos { text-align:left; background:#0d0d0d; border:1px solid #ffffff22; border-radius:12px; padding:16px; margin-top:20px; font-size:13px; color:#ccc; max-width:320px; }
.passos b { color:#fff; }
.voltar-baixar { margin-top:24px; color:#888; text-decoration:underline; }
</style></head>
<body>
<img class="icone-grande" src="{icone_app_url}">
<h2>Instalar o CHAT CPA</h2>
<p>Instale o CHAT CPA na tela inicial do seu celular pra abrir como um app, sem precisar do navegador.</p>
<button class="principal" id="botaoInstalar" style="display:none;" onclick="instalarApp()">Instalar agora</button>
<div class="passos" id="passosIOS" style="display:none;">
  <b>No iPhone (Safari):</b><br>
  1. Toque no botao de compartilhar (o quadrado com a seta pra cima)<br>
  2. Escolha "Adicionar a Tela de Inicio"<br>
  3. Toque em "Adicionar"
</div>
<div class="passos" id="passosGenerico">
  <b>Nao apareceu o botao?</b><br>
  No menu do navegador (tres pontinhos), procure por "Adicionar a tela inicial" ou "Instalar app".
</div>
<a class="voltar-baixar" href="/inicio">Voltar</a>
<script>
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/service-worker.js').catch(() => {}); }
let eventoInstalacao = null;
window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    eventoInstalacao = e;
    document.getElementById('botaoInstalar').style.display = 'inline-block';
});
async function instalarApp() {
    if (!eventoInstalacao) return;
    eventoInstalacao.prompt();
    await eventoInstalacao.userChoice;
    eventoInstalacao = null;
    document.getElementById('botaoInstalar').style.display = 'none';
}
const ehIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
if (ehIOS) document.getElementById('passosIOS').style.display = 'block';
</script>
</body></html>
"""


@app.route("/baixar")
def baixar():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    icone = obter_config("icone_app", "/static/logo.jpg")
    return PAGINA_BAIXAR.replace("{icone_app_url}", icone)


@app.route("/painel")
def painel():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    usuario = session["usuario"]
    linha = buscar_usuario(usuario)
    avatar = (linha["foto_perfil"] if linha and linha["foto_perfil"] else AVATAR_PADRAO + usuario)
    tag_html = html_tag(linha["tag"] if linha else None)
    return PAGINA.replace("{usuario}", usuario).replace("{selo_tag}", tag_html).replace("{avatar_url}", avatar)


@app.route("/logout")
def logout():
    session.pop("usuario", None)
    return redirect(url_for("login"))


@app.route("/chat", methods=["POST"])
def chat():
    if not session.get("usuario"):
        return jsonify({"resposta": "Sessao expirada, faca login novamente."}), 401
    dados = request.get_json()
    mensagem = dados.get("mensagem", "").strip()
    if not mensagem:
        return jsonify({"resposta": "Nao recebi nenhuma mensagem."})
    usuario = session["usuario"]
    conexao = obter_bd()
    conexao.execute("INSERT INTO mensagens (usuario, remetente, texto, criado_em) VALUES (?, ?, ?, ?)", (usuario, "usuario", mensagem, datetime.now().isoformat()))
    linhas = conexao.execute("SELECT remetente, texto FROM mensagens WHERE usuario = ? ORDER BY id DESC LIMIT 12", (usuario,)).fetchall()
    historico_mensagens = [{"role": "user" if l["remetente"] == "usuario" else "assistant", "content": l["texto"]} for l in reversed(linhas)]
    try:
        # Corrida entre todas as IAs de texto configuradas (Groq, Cerebras, OpenRouter,
        # Gemini) - usa a que responder primeiro, em vez de depender so da Groq.
        texto_resposta = gerar_resposta_ia([{"role": "system", "content": SISTEMA}] + historico_mensagens)
    except Exception as erro:
        conexao.close()
        print(f"[CHAT CPA] todas as IAs de texto falharam no /chat: {erro}")
        return jsonify({"resposta": "As IAs gratuitas estao indisponiveis no momento, tenta de novo em instantes."})
    conexao.execute("INSERT INTO mensagens (usuario, remetente, texto, criado_em) VALUES (?, ?, ?, ?)", (usuario, "jarvis", texto_resposta, datetime.now().isoformat()))
    conexao.commit()
    conexao.close()
    return jsonify({"resposta": texto_resposta})


@app.route("/imagem")
def imagem():
    if not session.get("usuario"):
        return jsonify({"url": ""}), 401
    prompt = request.args.get("prompt", "").strip()
    if not prompt:
        return jsonify({"url": ""})
    prompt_melhorado = prompt + ", highly detailed, professional quality, sharp focus, 4k"
    seed = random.randint(1, 999999)
    # Usa a corrida entre Pollinations e Hugging Face (gerar_imagem_bytes) em vez de
    # so montar um link do Pollinations pro navegador buscar sozinho - assim o
    # Hugging Face (se a chave estiver configurada) tambem entra na disputa, e a
    # imagem so volta pro app depois de confirmada (sem risco de link quebrado).
    conteudo = gerar_imagem_bytes(prompt_melhorado, seed)
    if not conteudo:
        return jsonify({"url": "", "erro": "Nao consegui gerar a imagem agora. Tenta de novo."})
    url = salvar_bytes_imagem(conteudo, "jpg")
    return jsonify({"url": url})


@app.route("/converter", methods=["POST"])
def converter():
    if not session.get("usuario"):
        return "Nao autorizado", 401
    if not Image:
        return "Biblioteca de imagem nao disponivel no servidor.", 500
    arquivo = request.files.get("arquivo")
    if not arquivo:
        return "Nenhum arquivo enviado.", 400
    formato = request.form.get("formato", "png").lower()
    largura = request.form.get("largura", "").strip()
    altura = request.form.get("altura", "").strip()
    try:
        img = Image.open(arquivo.stream)
        img = img.convert("RGB") if formato in ("jpeg", "jpg") else img.convert("RGBA")
        if largura and altura:
            img = img.resize((int(largura), int(altura)))
        saida = io.BytesIO()
        formato_pil = "JPEG" if formato in ("jpeg", "jpg") else formato.upper()
        img.save(saida, format=formato_pil)
        saida.seek(0)
        tipo_mime = {"png": "image/png", "gif": "image/gif", "jpeg": "image/jpeg", "jpg": "image/jpeg"}.get(formato, "image/png")
        return send_file(saida, mimetype=tipo_mime)
    except Exception as erro:
        return f"Erro ao converter: {erro}", 500


@app.route("/rede")
def rede():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    usuario = session["usuario"]
    eh_dev = eh_desenvolvedor(usuario)
    painel_admin_html = ""
    botao_engrenagem = ""
    if eh_dev:
        botao_engrenagem = '<span class="botao-engrenagem" onclick="document.getElementById(\'painelAdmin\').classList.toggle(\'aberto\')">&#9881;</span>'
        painel_admin_html = """
        <div class="painel-admin" id="painelAdmin">
          <div class="painel-admin-cabecalho">
            <b>Painel do desenvolvedor</b><span class="seta" onclick="document.getElementById('painelAdmin').classList.remove('aberto')" style="cursor:pointer;">&times;</span>
          </div>
          <div class="painel-admin-corpo">
            <div class="painel-admin-abas">
              <button class="ativa" onclick="mudarAbaAdmin('selo', this)">Selo</button>
              <button onclick="mudarAbaAdmin('tags', this)">Tags</button>
              <button onclick="mudarAbaAdmin('icones', this)">Icones</button>
              <button onclick="mudarAbaAdmin('ids', this)">IDs</button>
              <button onclick="mudarAbaAdmin('zap', this)">ZAP</button>
            </div>

            <div class="painel-admin-secao ativa" id="secaoAdmin-selo">
              Verificar por email ou ID:
              <div class="linha-admin">
                <input id="alvoVerificar" type="text" placeholder="email@exemplo.com ou #ID">
                <button class="acao" onclick="verificar()">Verificar / remover</button>
              </div>
              <div class="resultado-admin" id="resultadoVerificar"></div>
              <label class="rotulo-campo">Imagem customizada do selo (opcional)</label>
              <div class="linha-admin">
                <input id="seloArquivo" type="file" accept="image/*">
                <button class="acao" onclick="enviarConfig('selo_verificado_url','seloArquivo','resultadoSelo')">Salvar selo</button>
              </div>
              <div class="resultado-admin" id="resultadoSelo"></div>
            </div>

            <div class="painel-admin-secao" id="secaoAdmin-tags">
              Criar tag:
              <div class="linha-admin">
                <input id="tagNome" type="text" placeholder="nome da tag">
                <input id="tagCor" type="color" value="#ffffff">
                <input id="tagFoto" type="file" accept="image/*">
                <button class="acao" onclick="criarTag()">Criar</button>
              </div>
              <div class="resultado-admin" id="resultadoTag"></div>
              <label class="rotulo-campo">Dar tag a alguem (email ou ID)</label>
              <div class="linha-admin">
                <input id="tagAlvo" type="text" placeholder="email@exemplo.com ou #ID">
                <input id="tagNomeAtribuir" type="text" placeholder="nome da tag (vazio remove)">
                <button class="acao" onclick="atribuirTag()">Atribuir</button>
              </div>
              <div class="resultado-admin" id="resultadoAtribuir"></div>
            </div>

            <div class="painel-admin-secao" id="secaoAdmin-icones">
              Icone de cada app (aparece na tela inicial e nos apps):
              <div class="lista-icones">
                <div class="item-icone"><img id="previaIconeChatCpa" src="{icone_jarvis}"><input type="file" accept="image/*" id="arqIconeChatCpa" style="display:none" onchange="enviarConfig('icone_jarvis','arqIconeChatCpa','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeChatCpa').click()">CHAT CPA</button></div>
                <div class="item-icone"><img id="previaIconeChatCpaWeb" src="{icone_jarvisweb}"><input type="file" accept="image/*" id="arqIconeChatCpaWeb" style="display:none" onchange="enviarConfig('icone_jarvisweb','arqIconeChatCpaWeb','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeChatCpaWeb').click()">Social CPA</button></div>
                <div class="item-icone"><img id="previaIconeZap" src="{icone_zap}"><input type="file" accept="image/*" id="arqIconeZap" style="display:none" onchange="enviarConfig('icone_zap','arqIconeZap','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeZap').click()">ZAP</button></div>
                <div class="item-icone"><img id="previaIconeSuporte" src="{icone_suporte}"><input type="file" accept="image/*" id="arqIconeSuporte" style="display:none" onchange="enviarConfig('icone_suporte','arqIconeSuporte','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeSuporte').click()">Suporte</button></div>
                <div class="item-icone"><img id="previaIconeApp" src="{icone_app}"><input type="file" accept="image/*" id="arqIconeApp" style="display:none" onchange="enviarConfig('icone_app','arqIconeApp','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeApp').click()">Icone do app (instalar)</button></div>
              </div>
              <label class="rotulo-campo">Logo da tela de login/splash</label>
              <div class="linha-admin">
                <input id="logoArquivo" type="file" accept="image/*">
                <button class="acao" onclick="enviarConfig('logo_login','logoArquivo','resultadoLogo')">Salvar logo</button>
              </div>
              <div class="resultado-admin" id="resultadoLogo"></div>
              <label class="rotulo-campo">Plano de fundo da tela inicial</label>
              <div class="linha-admin">
                <input id="fundoArquivo" type="file" accept="image/*">
                <button class="acao" onclick="enviarConfig('fundo_inicio','fundoArquivo','resultadoFundo')">Salvar fundo</button>
              </div>
              <div class="resultado-admin" id="resultadoFundo"></div>
              <div class="resultado-admin" id="resultadoIcones"></div>
            </div>

            <div class="painel-admin-secao" id="secaoAdmin-ids">
              IDs de 2 a 11 sao reservados - so voce pode dar (o 1 e sempre seu).<br>
              A partir do 12, todo mundo recebe automaticamente em ordem.
              <label class="rotulo-campo">Definir ID de alguem (email ou ID atual)</label>
              <div class="linha-admin">
                <input id="idAlvo" type="text" placeholder="email@exemplo.com ou #ID atual">
                <input id="idNovo" type="number" placeholder="novo ID (2 a 11 ou qualquer livre)">
                <button class="acao" onclick="definirIdAdmin()">Definir</button>
              </div>
              <div class="resultado-admin" id="resultadoId"></div>
            </div>

            <div class="painel-admin-secao" id="secaoAdmin-zap">
              Buscar conversa por criptografia (frase):
              <div class="linha-admin">
                <input id="zapBuscaFrase" type="text" placeholder="parte da frase (opcional)">
                <button class="acao" onclick="buscarConversasZap()">Buscar</button>
              </div>
              <div id="listaConversasZap" style="margin-top:8px;font-size:12px;"></div>
              <div id="historicoZap" style="margin-top:8px;font-size:12px;max-height:240px;overflow-y:auto;"></div>
            </div>
          </div>
        </div>
        """
        painel_admin_html = (
            painel_admin_html
            .replace("{icone_jarvis}", obter_config("icone_jarvis", AVATAR_PADRAO + "jarvis"))
            .replace("{icone_jarvisweb}", obter_config("icone_jarvisweb", AVATAR_PADRAO + "jarvisweb"))
            .replace("{icone_zap}", obter_config("icone_zap", AVATAR_PADRAO + "jarviszap"))
            .replace("{icone_suporte}", obter_config("icone_suporte", AVATAR_PADRAO + "suporte"))
            .replace("{icone_app}", obter_config("icone_app", "/static/logo.jpg"))
        )
    linha_usuario = buscar_usuario(usuario)
    avatar_usuario = (linha_usuario["foto_perfil"] if linha_usuario and linha_usuario["foto_perfil"] else AVATAR_PADRAO + usuario)
    pagina = PAGINA_REDE.replace("{usuario}", usuario).replace("{painel_admin}", painel_admin_html)
    pagina = pagina.replace("{botao_engrenagem}", botao_engrenagem)
    pagina = pagina.replace("{icone_jarvis_nav}", obter_config("icone_jarvis", AVATAR_PADRAO + "jarvis"))
    pagina = pagina.replace("{avatar_usuario_nav}", avatar_usuario)
    return pagina


@app.route("/perfil/<nome_usuario>")
def perfil(nome_usuario):
    if not session.get("usuario"):
        return redirect(url_for("login"))
    usuario_logado = session["usuario"]
    linha_alvo = buscar_usuario(nome_usuario)
    if not linha_alvo:
        return "Usuario nao encontrado", 404
    nome_real = linha_alvo["usuario"]
    avatar = linha_alvo["foto_perfil"] or (AVATAR_PADRAO + nome_real)
    selo = selo_verificado_html(14) if linha_alvo["verificado"] else ""
    selo += html_tag(linha_alvo["tag"])
    banner_html = f'<img class="banner" src="{linha_alvo["banner"]}">' if linha_alvo["banner"] else ""
    bio_html = f'<div class="bio-perfil">{linha_alvo["bio"]}</div>' if linha_alvo["bio"] else ""
    conexao = obter_bd()
    posts = conexao.execute("SELECT * FROM posts WHERE usuario = ? ORDER BY id DESC", (nome_real,)).fetchall()
    qtd_seguidores = conexao.execute("SELECT COUNT(*) as c FROM seguidores WHERE seguido = ?", (nome_real,)).fetchone()["c"]
    qtd_seguindo = conexao.execute("SELECT COUNT(*) as c FROM seguidores WHERE seguidor = ?", (nome_real,)).fetchone()["c"]
    ja_segue = conexao.execute("SELECT 1 FROM seguidores WHERE seguidor = ? AND seguido = ?", (usuario_logado, nome_real)).fetchone()
    conexao.close()
    itens_grade = ""
    for p in posts:
        if p["imagem"]:
            itens_grade += f'<div class="grade-item"><img src="{p["imagem"]}" onclick="window.open(\'{p["imagem"]}\',\'_blank\')"></div>'
        elif p["video"]:
            itens_grade += f'<div class="grade-item"><video src="{p["video"]}"></video></div>'
        else:
            itens_grade += f'<div class="grade-item sem-midia">{(p["texto"] or "")[:40]}</div>'
    if nome_real == usuario_logado:
        botao_seguir = ""
        editor_perfil = f"""
        <div class="editar-perfil"><b>Editar perfil</b>
        <textarea id="novaBio" placeholder="Bio">{linha_alvo['bio'] or ''}</textarea>
        <label style="display:block;margin-top:8px;font-size:12px;color:#999;">Nova foto de perfil</label>
        <input id="novoAvatarArquivo" type="file" accept="image/*">
        <label style="display:block;margin-top:8px;font-size:12px;color:#999;">Novo banner</label>
        <input id="novoBannerArquivo" type="file" accept="image/*">
        <button onclick="salvarPerfil()">Salvar</button>
        <label style="display:block;margin-top:12px;font-size:12px;color:#999;">Seu ID permanente</label>
        <div class="linha-id">
          <input id="campoNovoId" type="number" value="{linha_alvo['id_publico'] or ''}">
          <button class="btn-secundario" onclick="mudarId()">Mudar ID</button>
        </div>
        <div class="msg-id" id="msgId"></div>
        </div>
        """
    else:
        classe_ativo = "ativo" if ja_segue else ""
        texto_botao = "Seguindo" if ja_segue else "Seguir"
        botao_seguir = f'<button class="botao-seguir {classe_ativo}" onclick="seguirPerfil(\'{nome_real}\')">{texto_botao}</button>'
        editor_perfil = ""
    pagina = PAGINA_PERFIL.replace("{nome_usuario}", nome_real).replace("{avatar_url}", avatar).replace("{selo}", selo)
    pagina = pagina.replace("{id_publico}", str(linha_alvo["id_publico"] or "-"))
    pagina = pagina.replace("{banner_html}", banner_html).replace("{bio_html}", bio_html)
    pagina = pagina.replace("{qtd_posts}", str(len(posts))).replace("{qtd_seguidores}", str(qtd_seguidores)).replace("{qtd_seguindo}", str(qtd_seguindo))
    pagina = pagina.replace("{botao_seguir}", botao_seguir).replace("{editor_perfil}", editor_perfil).replace("{itens_grade}", itens_grade)
    return pagina


@app.route("/perfil/editar", methods=["POST"])
def perfil_editar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    bio = request.form.get("bio", "").strip()
    conexao = obter_bd()
    foto_perfil = salvar_imagem(request.files.get("foto_perfil"))
    banner = salvar_imagem(request.files.get("banner"))
    if foto_perfil:
        conexao.execute("UPDATE usuarios SET foto_perfil = ? WHERE usuario = ?", (foto_perfil, usuario))
    if banner:
        conexao.execute("UPDATE usuarios SET banner = ? WHERE usuario = ?", (banner, usuario))
    conexao.execute("UPDATE usuarios SET bio = ? WHERE usuario = ?", (bio or None, usuario))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/perfil/mudar_id", methods=["POST"])
def perfil_mudar_id():
    if not session.get("usuario"):
        return jsonify({"ok": False, "erro": "Nao autenticado."}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    try:
        novo_id = int(dados.get("novo_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "erro": "ID invalido."})
    linha = buscar_usuario(usuario)
    eh_dono = linha and linha["email"] and linha["email"].strip().lower() == EMAIL_DONO.lower()
    if novo_id == 1 and not eh_dono:
        return jsonify({"ok": False, "erro": "O ID 1 e reservado."})
    if novo_id != 1 and novo_id <= 11 and not eh_desenvolvedor(usuario):
        return jsonify({"ok": False, "erro": "IDs de 2 a 11 sao reservados, so o dono pode dar."})
    conexao = obter_bd()
    em_uso = conexao.execute("SELECT 1 FROM usuarios WHERE id_publico = ? AND usuario != ?", (novo_id, usuario)).fetchone()
    if em_uso:
        conexao.close()
        return jsonify({"ok": False, "erro": "Esse ID ja esta em uso."})
    conexao.execute("UPDATE usuarios SET id_publico = ? WHERE usuario = ?", (novo_id, usuario))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "id_publico": novo_id})


@app.route("/admin/definir_id", methods=["POST"])
def admin_definir_id():
    """Painel do dono: define o ID de qualquer pessoa, inclusive os reservados (2 a 11)."""
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    dados = request.get_json() or {}
    alvo_valor = (dados.get("alvo") or "").strip()
    try:
        novo_id = int(str(dados.get("novo_id", "")).strip().lstrip("#"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "erro": "Digite um ID valido."})
    linha = buscar_usuario_por_email_ou_id(alvo_valor)
    if not linha:
        return jsonify({"ok": False, "erro": "Ninguem encontrado com esse email/ID."})
    if novo_id == 1 and linha["email"].strip().lower() != EMAIL_DONO.lower():
        return jsonify({"ok": False, "erro": "O ID 1 e exclusivo do dono."})
    conexao = obter_bd()
    em_uso = conexao.execute("SELECT 1 FROM usuarios WHERE id_publico = ? AND usuario != ? COLLATE NOCASE", (novo_id, linha["usuario"])).fetchone()
    if em_uso:
        conexao.close()
        return jsonify({"ok": False, "erro": "Esse ID ja esta em uso."})
    conexao.execute("UPDATE usuarios SET id_publico = ? WHERE usuario = ? COLLATE NOCASE", (novo_id, linha["usuario"]))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "usuario": linha["usuario"], "id_publico": novo_id})


@app.route("/auth/sugerir_id")
def auth_sugerir_id():
    conexao = obter_bd()
    novo_id = gerar_id_publico(conexao)
    conexao.close()
    return jsonify({"id_publico": novo_id})


@app.route("/rede/postar", methods=["POST"])
def rede_postar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    texto = request.form.get("texto", "").strip()
    video = request.form.get("video", "").strip()
    imagem_link = request.form.get("imagem_link", "").strip()
    caminho_imagem = salvar_imagem(request.files.get("imagem"))
    if not caminho_imagem and imagem_link:
        if not (imagem_link.startswith("http://") or imagem_link.startswith("https://")):
            return jsonify({"ok": False, "erro": "O link de imagem precisa comecar com http:// ou https://"}), 400
        caminho_imagem = imagem_link
    if not texto and not caminho_imagem and not video:
        return jsonify({"ok": False, "erro": "Escreva algo, escolha uma foto ou cole um link antes de postar."}), 400
    conexao = obter_bd()
    conexao.execute("INSERT INTO posts (usuario, texto, imagem, video, criado_em) VALUES (?, ?, ?, ?, ?)", (usuario, texto, caminho_imagem, video or None, datetime.now().isoformat()))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/rede/feed")
def rede_feed():
    if not session.get("usuario"):
        return jsonify([]), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    posts = conexao.execute("SELECT * FROM posts ORDER BY id DESC LIMIT 50").fetchall()
    resultado = []
    for p in posts:
        curtidas = conexao.execute("SELECT COUNT(*) as c FROM curtidas WHERE post_id = ?", (p["id"],)).fetchone()["c"]
        curtido = conexao.execute("SELECT 1 FROM curtidas WHERE post_id = ? AND usuario = ?", (p["id"], usuario)).fetchone() is not None
        comentarios = conexao.execute("SELECT usuario, texto FROM comentarios WHERE post_id = ? ORDER BY id ASC", (p["id"],)).fetchall()
        seguindo = conexao.execute("SELECT 1 FROM seguidores WHERE seguidor = ? AND seguido = ?", (usuario, p["usuario"])).fetchone() is not None
        salvo = conexao.execute("SELECT 1 FROM salvos WHERE usuario = ? AND post_id = ?", (usuario, p["id"])).fetchone() is not None
        linha_autor = conexao.execute("SELECT verificado, foto_perfil, tag FROM usuarios WHERE usuario = ?", (p["usuario"],)).fetchone()
        verificado = bool(linha_autor and linha_autor["verificado"])
        avatar = (linha_autor["foto_perfil"] if linha_autor and linha_autor["foto_perfil"] else AVATAR_PADRAO + p["usuario"])
        tag_html = html_tag(linha_autor["tag"] if linha_autor else None)
        resultado.append({
            "id": p["id"], "usuario": p["usuario"], "texto": p["texto"], "imagem": p["imagem"], "video": p["video"],
            "avatar": avatar, "curtidas": curtidas, "curtido": curtido, "seguindo": seguindo, "verificado": verificado,
            "salvo": salvo, "selo_html": selo_verificado_html(14) if verificado else "",
            "tag_html": tag_html,
            "comentarios": [{"usuario": c["usuario"], "texto": c["texto"]} for c in comentarios],
        })
    conexao.close()
    return jsonify(resultado)


@app.route("/rede/curtir", methods=["POST"])
def rede_curtir():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    post_id = request.get_json().get("post_id")
    conexao = obter_bd()
    ja_curtiu = conexao.execute("SELECT 1 FROM curtidas WHERE post_id = ? AND usuario = ?", (post_id, usuario)).fetchone()
    if ja_curtiu:
        conexao.execute("DELETE FROM curtidas WHERE post_id = ? AND usuario = ?", (post_id, usuario))
    else:
        conexao.execute("INSERT INTO curtidas (post_id, usuario) VALUES (?, ?)", (post_id, usuario))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/rede/salvar", methods=["POST"])
def rede_salvar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    post_id = request.get_json().get("post_id")
    conexao = obter_bd()
    ja_salvou = conexao.execute("SELECT 1 FROM salvos WHERE post_id = ? AND usuario = ?", (post_id, usuario)).fetchone()
    if ja_salvou:
        conexao.execute("DELETE FROM salvos WHERE post_id = ? AND usuario = ?", (post_id, usuario))
    else:
        conexao.execute("INSERT INTO salvos (post_id, usuario, criado_em) VALUES (?, ?, ?)", (post_id, usuario, datetime.now().isoformat()))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/rede/comentar", methods=["POST"])
def rede_comentar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json()
    post_id = dados.get("post_id")
    texto = dados.get("texto", "").strip()
    if not texto:
        return jsonify({"ok": False})
    conexao = obter_bd()
    conexao.execute("INSERT INTO comentarios (post_id, usuario, texto, criado_em) VALUES (?, ?, ?, ?)", (post_id, usuario, texto, datetime.now().isoformat()))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/rede/seguir", methods=["POST"])
def rede_seguir():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    alvo = request.get_json().get("alvo", "").strip()
    if not alvo or alvo == usuario:
        return jsonify({"ok": False})
    conexao = obter_bd()
    ja_segue = conexao.execute("SELECT 1 FROM seguidores WHERE seguidor = ? AND seguido = ?", (usuario, alvo)).fetchone()
    if ja_segue:
        conexao.execute("DELETE FROM seguidores WHERE seguidor = ? AND seguido = ?", (usuario, alvo))
    else:
        conexao.execute("INSERT INTO seguidores (seguidor, seguido) VALUES (?, ?)", (usuario, alvo))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/rede/verificar", methods=["POST"])
def rede_verificar():
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    dados = request.get_json()
    alvo_valor = dados.get("alvo", "").strip()
    linha = buscar_usuario_por_email_ou_id(alvo_valor)
    if not linha:
        return jsonify({"ok": False, "erro": "Ninguem encontrado com esse email/ID."})
    conexao = obter_bd()
    novo_estado = 0 if linha["verificado"] else 1
    conexao.execute("UPDATE usuarios SET verificado = ? WHERE usuario = ? COLLATE NOCASE", (novo_estado, linha["usuario"]))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "verificado": bool(novo_estado)})


@app.route("/rede/criar_tag", methods=["POST"])
def rede_criar_tag():
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    nome = request.form.get("nome", "").strip()
    cor = request.form.get("cor", "#ffffff").strip()
    if not nome:
        return jsonify({"ok": False, "erro": "Nome obrigatorio."})
    foto = salvar_imagem(request.files.get("foto"))
    conexao = obter_bd()
    existente = conexao.execute("SELECT foto FROM tags WHERE nome = ?", (nome,)).fetchone()
    if not foto and existente:
        foto = existente["foto"]
    conexao.execute("INSERT OR REPLACE INTO tags (nome, cor, foto) VALUES (?, ?, ?)", (nome, cor, foto))
    conexao.commit()
    todas = conexao.execute("SELECT nome FROM tags ORDER BY nome").fetchall()
    conexao.close()
    return jsonify({"ok": True, "tags": [t["nome"] for t in todas]})


@app.route("/rede/tags")
def rede_tags():
    if not session.get("usuario"):
        return jsonify([]), 401
    conexao = obter_bd()
    todas = conexao.execute("SELECT nome FROM tags ORDER BY nome").fetchall()
    conexao.close()
    return jsonify([t["nome"] for t in todas])


@app.route("/rede/atribuir_tag", methods=["POST"])
def rede_atribuir_tag():
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    dados = request.get_json()
    alvo_valor = dados.get("alvo", "").strip()
    tag = dados.get("tag", "").strip()
    linha = buscar_usuario_por_email_ou_id(alvo_valor)
    if not linha:
        return jsonify({"ok": False, "erro": "Ninguem encontrado com esse email/ID."})
    conexao = obter_bd()
    conexao.execute("UPDATE usuarios SET tag = ? WHERE usuario = ? COLLATE NOCASE", (tag or None, linha["usuario"]))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/admin/config", methods=["POST"])
def admin_config():
    """Painel do dono: define os icones de cada app e a imagem do selo verificado."""
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    chave = request.form.get("chave", "").strip()
    chaves_permitidas = {
        "icone_jarvis", "icone_jarvisweb", "icone_suporte", "icone_zap", "selo_verificado_url", "logo_login", "icone_app",
        "fundo_inicio",
    }
    if chave not in chaves_permitidas:
        return jsonify({"ok": False, "erro": "Configuracao invalida."})
    arquivo = request.files.get("arquivo")
    if not arquivo:
        return jsonify({"ok": False, "erro": "Escolha uma imagem."})
    url = salvar_imagem(arquivo)
    if not url:
        return jsonify({"ok": False, "erro": "Nao foi possivel enviar a imagem."})
    definir_config(chave, url)
    return jsonify({"ok": True, "url": url})


@app.route("/admin/zap/conversas")
def admin_zap_conversas():
    """Painel do dono: lista conversas do ZAP e permite buscar pela frase de criptografia."""
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify([]), 403
    busca = (request.args.get("frase") or "").strip().lower()
    conexao = obter_bd()
    if busca:
        linhas = conexao.execute(
            "SELECT * FROM zap_conversas WHERE LOWER(frase_cripto) LIKE ? ORDER BY criado_em DESC", (f"%{busca}%",)
        ).fetchall()
    else:
        linhas = conexao.execute("SELECT * FROM zap_conversas ORDER BY criado_em DESC LIMIT 50").fetchall()
    conexao.close()
    return jsonify([
        {"conversa": l["conversa"], "participantes": l["conversa"].split("|"), "frase": l["frase_cripto"], "ativada_por": l["ativada_por"]}
        for l in linhas
    ])


@app.route("/admin/zap/historico/<conversa>")
def admin_zap_historico(conversa):
    """Painel do dono: ve o historico completo (ja descriptografado) de uma conversa."""
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"mensagens": []}), 403
    conexao = obter_bd()
    conversa_info = conexao.execute("SELECT frase_cripto FROM zap_conversas WHERE conversa = ?", (conversa,)).fetchone()
    frase = conversa_info["frase_cripto"] if conversa_info else None
    linhas = conexao.execute("SELECT * FROM zap_mensagens WHERE conversa = ? ORDER BY id ASC LIMIT 500", (conversa,)).fetchall()
    conexao.close()
    mensagens = []
    for l in linhas:
        conteudo = l["conteudo"]
        if l["criptografado"] and frase:
            decifrado = descriptografar_texto(conteudo, frase)
            conteudo = decifrado if decifrado is not None else "[nao foi possivel decifrar]"
        mensagens.append({"id": l["id"], "remetente": l["remetente"], "tipo": l["tipo"], "conteudo": conteudo, "criado_em": l["criado_em"]})
    return jsonify({"mensagens": mensagens})


@app.route("/suporte")
def suporte():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    usuario = session["usuario"]
    conexao = obter_bd()
    eh_agente = conexao.execute("SELECT 1 FROM agentes_suporte WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchone() is not None
    eh_dev = eh_desenvolvedor(usuario)
    conexao.close()
    painel_admin_html = ""
    if eh_dev:
        painel_admin_html = """
        <div class="painel-admin"><b>Adicionar atendente de suporte</b><br>
        <input id="idAgente" placeholder="#ID da conta">
        <button onclick="adicionarAgente()">Adicionar</button>
        <div id="resultadoAgente" style="margin-top:6px;"></div></div>
        """
    pagina = PAGINA_SUPORTE.replace("{usuario}", usuario)
    pagina = pagina.replace("{eh_agente}", "true" if eh_agente else "false")
    pagina = pagina.replace("{painel_admin}", painel_admin_html)
    return pagina


@app.route("/suporte/agente", methods=["POST"])
def suporte_agente():
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    dados = request.get_json() or {}
    valor_id = (dados.get("id_publico") or "").strip().lstrip("#")
    try:
        id_publico = int(valor_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "erro": "Digite um ID valido."})
    alvo = buscar_usuario_por_id_publico(id_publico)
    if not alvo:
        return jsonify({"ok": False, "erro": "Nenhuma conta com esse ID."})
    conexao = obter_bd()
    conexao.execute("INSERT OR IGNORE INTO agentes_suporte (usuario) VALUES (?)", (alvo["usuario"],))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "usuario": alvo["usuario"]})


@app.route("/suporte/meu_ticket")
def suporte_meu_ticket():
    if not session.get("usuario"):
        return jsonify({}), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    ticket = conexao.execute("SELECT * FROM tickets_suporte WHERE usuario = ? AND status != 'fechado' ORDER BY id DESC LIMIT 1", (usuario,)).fetchone()
    if not ticket:
        conexao.close()
        return jsonify({"ticket_id": None, "mensagens": []})
    mensagens = conexao.execute("SELECT remetente, texto FROM mensagens_suporte WHERE ticket_id = ? ORDER BY id ASC", (ticket["id"],)).fetchall()
    conexao.close()
    return jsonify({"ticket_id": ticket["id"], "atendente": ticket["atendente"], "mensagens": [{"remetente": m["remetente"], "texto": m["texto"]} for m in mensagens]})


@app.route("/suporte/abrir_chamado", methods=["POST"])
def suporte_abrir_chamado():
    """Cria um chamado de suporte vazio (sem precisar mandar mensagem primeiro)."""
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    ticket = conexao.execute("SELECT id FROM tickets_suporte WHERE usuario = ? AND status != 'fechado' ORDER BY id DESC LIMIT 1", (usuario,)).fetchone()
    if ticket:
        conexao.close()
        return jsonify({"ok": True, "ticket_id": ticket["id"], "ja_existia": True})
    conexao.execute("INSERT INTO tickets_suporte (usuario, status, criado_em) VALUES (?, 'aberto', ?)", (usuario, datetime.now().isoformat()))
    conexao.commit()
    ticket_id = conexao.execute("SELECT id FROM tickets_suporte WHERE usuario = ? ORDER BY id DESC LIMIT 1", (usuario,)).fetchone()["id"]
    conexao.close()
    return jsonify({"ok": True, "ticket_id": ticket_id, "ja_existia": False})


@app.route("/suporte/enviar", methods=["POST"])
def suporte_enviar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json()
    texto = dados.get("texto", "").strip()
    if not texto:
        return jsonify({"ok": False})
    conexao = obter_bd()
    ticket = conexao.execute("SELECT * FROM tickets_suporte WHERE usuario = ? AND status != 'fechado' ORDER BY id DESC LIMIT 1", (usuario,)).fetchone()
    if not ticket:
        conexao.execute("INSERT INTO tickets_suporte (usuario, status, criado_em) VALUES (?, 'aberto', ?)", (usuario, datetime.now().isoformat()))
        conexao.commit()
        ticket_id = conexao.execute("SELECT id FROM tickets_suporte WHERE usuario = ? ORDER BY id DESC LIMIT 1", (usuario,)).fetchone()["id"]
    else:
        ticket_id = ticket["id"]
    conexao.execute("INSERT INTO mensagens_suporte (ticket_id, remetente, texto, criado_em) VALUES (?, ?, ?, ?)", (ticket_id, usuario, texto, datetime.now().isoformat()))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "ticket_id": ticket_id})


@app.route("/suporte/tickets")
def suporte_tickets():
    if not session.get("usuario"):
        return jsonify([]), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    eh_agente = conexao.execute("SELECT 1 FROM agentes_suporte WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchone() is not None
    if not eh_agente:
        conexao.close()
        return jsonify([])
    tickets = conexao.execute("SELECT * FROM tickets_suporte WHERE status != 'fechado' ORDER BY id DESC").fetchall()
    conexao.close()
    return jsonify([{"id": t["id"], "usuario": t["usuario"], "atendente": t["atendente"], "status": t["status"]} for t in tickets])


@app.route("/suporte/ticket/<int:ticket_id>")
def suporte_ticket_detalhe(ticket_id):
    if not session.get("usuario"):
        return jsonify({}), 401
    conexao = obter_bd()
    ticket = conexao.execute("SELECT * FROM tickets_suporte WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        conexao.close()
        return jsonify({}), 404
    mensagens = conexao.execute("SELECT remetente, texto FROM mensagens_suporte WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,)).fetchall()
    conexao.close()
    return jsonify({"usuario": ticket["usuario"], "atendente": ticket["atendente"], "mensagens": [{"remetente": m["remetente"], "texto": m["texto"]} for m in mensagens]})


@app.route("/suporte/responder", methods=["POST"])
def suporte_responder():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    eh_agente = conexao.execute("SELECT 1 FROM agentes_suporte WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchone() is not None
    if not eh_agente:
        conexao.close()
        return jsonify({"ok": False}), 403
    dados = request.get_json()
    ticket_id = dados.get("ticket_id")
    texto = dados.get("texto", "").strip()
    if not texto:
        conexao.close()
        return jsonify({"ok": False})
    ticket = conexao.execute("SELECT * FROM tickets_suporte WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        conexao.close()
        return jsonify({"ok": False})
    if not ticket["atendente"]:
        conexao.execute("UPDATE tickets_suporte SET atendente = ?, status = 'atribuido' WHERE id = ?", (usuario, ticket_id))
    conexao.execute("INSERT INTO mensagens_suporte (ticket_id, remetente, texto, criado_em) VALUES (?, ?, ?, ?)", (ticket_id, usuario, texto, datetime.now().isoformat()))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# ================= ROTAS DO ZAP =================

@app.route("/zap")
def zap():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    usuario = session["usuario"]
    marcar_atividade(usuario)
    linha = buscar_usuario(usuario)
    if linha and linha["bloqueado"]:
        return PAGINA_BLOQUEADO
    return PAGINA_ZAP


@app.route("/zap/contatos")
def zap_contatos():
    if not session.get("usuario"):
        return jsonify([]), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    linhas = conexao.execute(
        "SELECT contato FROM zap_contatos WHERE usuario = ? COLLATE NOCASE ORDER BY criado_em DESC", (usuario,)
    ).fetchall()
    resultado = []
    for l in linhas:
        u = buscar_usuario(l["contato"])
        if not u:
            continue
        avatar = u["foto_perfil"] if u["foto_perfil"] else AVATAR_PADRAO + u["usuario"]
        resultado.append({"usuario": u["usuario"], "id_publico": u["id_publico"], "avatar": avatar, "bloqueado": usuario_bloqueou(usuario, u["usuario"])})
    conexao.close()
    return jsonify(resultado)


@app.route("/zap/adicionar_contato", methods=["POST"])
def zap_adicionar_contato():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    try:
        id_publico = int(str(dados.get("id", "")).strip().lstrip("#"))
    except ValueError:
        return jsonify({"ok": False, "erro": "Digite um ID valido."})
    alvo = buscar_usuario_por_id_publico(id_publico)
    if not alvo:
        return jsonify({"ok": False, "erro": "Nao existe ninguem com esse ID."})
    if alvo["usuario"].lower() == usuario.lower():
        return jsonify({"ok": False, "erro": "Esse ID e o seu."})
    conexao = obter_bd()
    conexao.execute(
        "INSERT OR IGNORE INTO zap_contatos (usuario, contato, criado_em) VALUES (?, ?, ?)",
        (usuario, alvo["usuario"], datetime.now().isoformat()),
    )
    # some pros dois lados automaticamente - a outra pessoa nao precisa adicionar de volta
    conexao.execute(
        "INSERT OR IGNORE INTO zap_contatos (usuario, contato, criado_em) VALUES (?, ?, ?)",
        (alvo["usuario"], usuario, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/mensagens/<contato>")
def zap_mensagens(contato):
    if not session.get("usuario"):
        return jsonify({"mensagens": []}), 401
    usuario = session["usuario"]
    alvo = buscar_usuario(contato)
    if not alvo:
        return jsonify({"mensagens": [], "criptografado": False})
    conversa = id_conversa(usuario, alvo["usuario"])
    conexao = obter_bd()
    conversa_info = conexao.execute("SELECT frase_cripto FROM zap_conversas WHERE conversa = ?", (conversa,)).fetchone()
    frase = conversa_info["frase_cripto"] if conversa_info else None
    linhas = conexao.execute(
        "SELECT * FROM zap_mensagens WHERE conversa = ? ORDER BY id ASC LIMIT 300", (conversa,)
    ).fetchall()
    conexao.close()
    mensagens = []
    for l in linhas:
        conteudo = l["conteudo"]
        if l["criptografado"] and frase:
            decifrado = descriptografar_texto(conteudo, frase)
            conteudo = decifrado if decifrado is not None else "[mensagem criptografada]"
        mensagens.append({
            "id": l["id"], "tipo": l["tipo"], "conteudo": conteudo,
            "minha": l["remetente"].lower() == usuario.lower(),
        })
    return jsonify({"mensagens": mensagens, "criptografado": bool(frase), "bloqueado": usuario_bloqueou(usuario, alvo["usuario"])})


def ativar_criptografia(conversa, usuario, frase):
    """Ativa (ou troca) a frase de criptografia de uma conversa e registra um aviso do sistema."""
    frase = (frase or "").strip()
    if not frase:
        return False
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO zap_conversas (conversa, frase_cripto, ativada_por, criado_em) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(conversa) DO UPDATE SET frase_cripto = excluded.frase_cripto, ativada_por = excluded.ativada_por",
        (conversa, frase, usuario, datetime.now().isoformat()),
    )
    aviso = f"&#128274; {usuario} ativou a criptografia desta conversa. O bot do CHAT CPA vai cifrar as mensagens de texto a partir de agora."
    conexao.execute(
        "INSERT INTO zap_mensagens (conversa, remetente, destinatario, tipo, conteudo, criptografado, criado_em) VALUES (?, 'jarvis', ?, 'sistema', ?, 0, ?)",
        (conversa, conversa, aviso, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return True


def _processar_comando_criptografia(conversa, usuario, texto):
    """Se a mensagem for 'criptografia de <algo>', ativa a criptografia da conversa.
    Mantido por compatibilidade - o jeito recomendado agora e o botao de cadeado no topo do chat."""
    texto_normalizado = texto.strip().lower()
    if not texto_normalizado.startswith("criptografia de "):
        return None
    frase = texto.strip()[len("criptografia de "):].strip()
    if not frase:
        return None
    ativar_criptografia(conversa, usuario, frase)
    return frase


def usuario_bloqueou(usuario, contato):
    """True se 'usuario' bloqueou 'contato' OU 'contato' bloqueou 'usuario' (bloqueio e sempre dos dois lados)."""
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT 1 FROM zap_bloqueios WHERE (usuario = ? AND bloqueado = ?) OR (usuario = ? AND bloqueado = ?)",
        (usuario, contato, contato, usuario),
    ).fetchone()
    conexao.close()
    return linha is not None


@app.route("/zap/criptografar", methods=["POST"])
def zap_criptografar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    contato = (dados.get("contato") or "").strip()
    frase = (dados.get("frase") or "").strip()
    alvo = buscar_usuario(contato)
    if not alvo or not frase:
        return jsonify({"ok": False, "erro": "Dados invalidos."})
    conversa = id_conversa(usuario, alvo["usuario"])
    ativar_criptografia(conversa, usuario, frase)
    return jsonify({"ok": True})


@app.route("/zap/bloquear", methods=["POST"])
def zap_bloquear():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    contato = (dados.get("contato") or "").strip()
    alvo = buscar_usuario(contato)
    if not alvo:
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    conexao = obter_bd()
    ja_bloqueado = conexao.execute(
        "SELECT 1 FROM zap_bloqueios WHERE usuario = ? AND bloqueado = ?", (usuario, alvo["usuario"])
    ).fetchone()
    if ja_bloqueado:
        conexao.execute("DELETE FROM zap_bloqueios WHERE usuario = ? AND bloqueado = ?", (usuario, alvo["usuario"]))
        bloqueado_agora = False
    else:
        conexao.execute(
            "INSERT OR IGNORE INTO zap_bloqueios (usuario, bloqueado, criado_em) VALUES (?, ?, ?)",
            (usuario, alvo["usuario"], datetime.now().isoformat()),
        )
        bloqueado_agora = True
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "bloqueado": bloqueado_agora})


@app.route("/zap/enviar", methods=["POST"])
def zap_enviar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    linha_usuario = buscar_usuario(usuario)
    if linha_usuario and linha_usuario["bloqueado"]:
        return jsonify({"ok": False, "bloqueado": True, "erro": "Sua conta esta bloqueada no ZAP."})
    dados = request.get_json() or {}
    contato = (dados.get("contato") or "").strip()
    tipo = dados.get("tipo", "texto")
    conteudo = (dados.get("conteudo") or "").strip()
    alvo = buscar_usuario(contato)
    if not alvo or not conteudo:
        return jsonify({"ok": False, "erro": "Dados invalidos."})
    if usuario_bloqueou(usuario, alvo["usuario"]):
        return jsonify({"ok": False, "erro": "Voce nao pode enviar mensagem para este contato (bloqueado)."})
    conversa = id_conversa(usuario, alvo["usuario"])

    if tipo == "texto":
        frase_ativada = _processar_comando_criptografia(conversa, usuario, conteudo)
        if frase_ativada is not None:
            return jsonify({"ok": True, "comando": True})
        if mensagem_contem_conteudo_proibido(conteudo):
            avisos, bloqueado = aplicar_moderacao(usuario)
            conexao = obter_bd()
            aviso = ("O bot do CHAT CPA bloqueou esta mensagem por conter conteudo proibido (adulto/+18 ou de terror/ameaca). "
                     f"Aviso {avisos}/3." + (" Conta bloqueada no ZAP." if bloqueado else ""))
            conexao.execute(
                "INSERT INTO zap_mensagens (conversa, remetente, destinatario, tipo, conteudo, criptografado, criado_em) VALUES (?, 'jarvis', ?, 'sistema', ?, 0, ?)",
                (conversa, conversa, aviso, datetime.now().isoformat()),
            )
            conexao.commit()
            conexao.close()
            return jsonify({"ok": False, "erro": "Mensagem bloqueada pelo bot do CHAT CPA.", "bloqueado": bloqueado})

    conexao = obter_bd()
    conversa_info = conexao.execute("SELECT frase_cripto FROM zap_conversas WHERE conversa = ?", (conversa,)).fetchone()
    frase = conversa_info["frase_cripto"] if conversa_info else None
    criptografado = 0
    conteudo_salvo = conteudo
    if tipo == "texto" and frase:
        conteudo_salvo, ok = criptografar_texto(conteudo, frase)
        criptografado = 1 if ok else 0
    conexao.execute(
        "INSERT INTO zap_mensagens (conversa, remetente, destinatario, tipo, conteudo, criptografado, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (conversa, usuario, alvo["usuario"], tipo, conteudo_salvo, criptografado, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/enviar_arquivo", methods=["POST"])
def zap_enviar_arquivo():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    linha_usuario = buscar_usuario(usuario)
    if linha_usuario and linha_usuario["bloqueado"]:
        return jsonify({"ok": False, "bloqueado": True, "erro": "Sua conta esta bloqueada no ZAP."})
    contato = (request.form.get("contato") or "").strip()
    tipo = request.form.get("tipo", "imagem")
    alvo = buscar_usuario(contato)
    arquivo = request.files.get("arquivo")
    if not alvo or not arquivo:
        return jsonify({"ok": False, "erro": "Dados invalidos."})
    if usuario_bloqueou(usuario, alvo["usuario"]):
        return jsonify({"ok": False, "erro": "Voce nao pode enviar mensagem para este contato (bloqueado)."})
    # Aviso: o bot do CHAT CPA ainda nao analisa o CONTEUDO de imagens/audios/videos
    # (isso exigiria um servico externo de moderacao de midia). Por enquanto, a
    # protecao para midia e o botao "Denunciar", que bloqueia a conta apos varias
    # denuncias confirmadas (veja /zap/denunciar).
    url = salvar_midia_zap(arquivo)
    if not url:
        return jsonify({"ok": False, "erro": "Nao foi possivel enviar o arquivo."})
    conversa = id_conversa(usuario, alvo["usuario"])
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO zap_mensagens (conversa, remetente, destinatario, tipo, conteudo, criptografado, criado_em) VALUES (?, ?, ?, ?, ?, 0, ?)",
        (conversa, usuario, alvo["usuario"], tipo, url, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "url": url})


# ---------- Ligacoes de voz (sinalizacao WebRTC por polling) ----------
@app.route("/zap/chamada/iniciar", methods=["POST"])
def zap_chamada_iniciar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    contato = (dados.get("contato") or "").strip()
    oferta = dados.get("oferta")
    alvo = buscar_usuario(contato)
    if not alvo or not oferta:
        return jsonify({"ok": False, "erro": "Dados invalidos."})
    conexao = obter_bd()
    # nao deixa duas chamadas simultaneas pendentes entre as mesmas duas pessoas
    conexao.execute(
        "UPDATE zap_chamadas SET status = 'encerrada' WHERE status IN ('chamando','aceita') "
        "AND ((quem_liga = ? AND quem_recebe = ?) OR (quem_liga = ? AND quem_recebe = ?))",
        (usuario, alvo["usuario"], alvo["usuario"], usuario),
    )
    cursor = conexao.execute(
        "INSERT INTO zap_chamadas (quem_liga, quem_recebe, oferta, status, criado_em) VALUES (?, ?, ?, 'chamando', ?)",
        (usuario, alvo["usuario"], json.dumps(oferta), datetime.now().isoformat()),
    )
    conexao.commit()
    chamada_id = cursor.lastrowid
    conexao.close()
    return jsonify({"ok": True, "chamada_id": chamada_id})


@app.route("/zap/chamada/pendente")
def zap_chamada_pendente():
    """Usado pelo lado que recebe: pergunta periodicamente se tem uma ligacao chegando."""
    if not session.get("usuario"):
        return jsonify({"chamada": None}), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    # ligacoes esquecidas (ex: a pessoa fechou a aba) nao ficam tocando pra sempre
    conexao.execute(
        "UPDATE zap_chamadas SET status = 'encerrada' WHERE status = 'chamando' AND criado_em < ?",
        ((datetime.now() - timedelta(seconds=45)).isoformat(),),
    )
    conexao.commit()
    linha = conexao.execute(
        "SELECT * FROM zap_chamadas WHERE quem_recebe = ? COLLATE NOCASE AND status = 'chamando' ORDER BY id DESC LIMIT 1",
        (usuario,),
    ).fetchone()
    conexao.close()
    if not linha:
        return jsonify({"chamada": None})
    return jsonify({"chamada": {"id": linha["id"], "de": linha["quem_liga"], "oferta": json.loads(linha["oferta"])}})


@app.route("/zap/chamada/responder", methods=["POST"])
def zap_chamada_responder():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    chamada_id = dados.get("chamada_id")
    resposta = dados.get("resposta")
    aceitar = dados.get("aceitar", True)
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM zap_chamadas WHERE id = ? AND quem_recebe = ? COLLATE NOCASE", (chamada_id, usuario)).fetchone()
    if not linha:
        conexao.close()
        return jsonify({"ok": False, "erro": "Chamada nao encontrada."})
    if not aceitar:
        conexao.execute("UPDATE zap_chamadas SET status = 'recusada' WHERE id = ?", (chamada_id,))
    else:
        conexao.execute("UPDATE zap_chamadas SET status = 'aceita', resposta = ? WHERE id = ?", (json.dumps(resposta), chamada_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/chamada/status/<int:chamada_id>")
def zap_chamada_status(chamada_id):
    """Usado por quem ligou: espera a resposta (SDP answer) de quem recebeu."""
    if not session.get("usuario"):
        return jsonify({"status": "encerrada"}), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM zap_chamadas WHERE id = ? AND quem_liga = ? COLLATE NOCASE", (chamada_id, usuario)).fetchone()
    conexao.close()
    if not linha:
        return jsonify({"status": "encerrada"})
    resultado = {"status": linha["status"]}
    if linha["resposta"]:
        resultado["resposta"] = json.loads(linha["resposta"])
    return jsonify(resultado)


@app.route("/zap/chamada/candidato", methods=["POST"])
def zap_chamada_candidato():
    """Cada lado envia seus candidatos ICE (rota de rede) conforme o navegador os descobre."""
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    chamada_id = dados.get("chamada_id")
    candidato = dados.get("candidato")
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM zap_chamadas WHERE id = ?", (chamada_id,)).fetchone()
    if not linha:
        conexao.close()
        return jsonify({"ok": False})
    if linha["quem_liga"].lower() == usuario.lower():
        lista = json.loads(linha["candidatos_liga"] or "[]")
        lista.append(candidato)
        conexao.execute("UPDATE zap_chamadas SET candidatos_liga = ? WHERE id = ?", (json.dumps(lista), chamada_id))
    elif linha["quem_recebe"].lower() == usuario.lower():
        lista = json.loads(linha["candidatos_recebe"] or "[]")
        lista.append(candidato)
        conexao.execute("UPDATE zap_chamadas SET candidatos_recebe = ? WHERE id = ?", (json.dumps(lista), chamada_id))
    else:
        conexao.close()
        return jsonify({"ok": False})
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/chamada/candidatos/<int:chamada_id>")
def zap_chamada_candidatos(chamada_id):
    """Cada lado busca os candidatos ICE que o OUTRO lado ja mandou."""
    if not session.get("usuario"):
        return jsonify({"candidatos": [], "status": "encerrada"}), 401
    usuario = session["usuario"]
    desde = int(request.args.get("desde", 0))
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM zap_chamadas WHERE id = ?", (chamada_id,)).fetchone()
    conexao.close()
    if not linha:
        return jsonify({"candidatos": [], "status": "encerrada"})
    if linha["quem_liga"].lower() == usuario.lower():
        lista = json.loads(linha["candidatos_recebe"] or "[]")
    else:
        lista = json.loads(linha["candidatos_liga"] or "[]")
    return jsonify({"candidatos": lista[desde:], "total": len(lista), "status": linha["status"]})


@app.route("/zap/chamada/encerrar", methods=["POST"])
def zap_chamada_encerrar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    chamada_id = dados.get("chamada_id")
    conexao = obter_bd()
    conexao.execute(
        "UPDATE zap_chamadas SET status = 'encerrada' WHERE id = ? AND (quem_liga = ? COLLATE NOCASE OR quem_recebe = ? COLLATE NOCASE)",
        (chamada_id, usuario, usuario),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/denunciar", methods=["POST"])
def zap_denunciar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    try:
        mensagem_id = int(dados.get("mensagem_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False})
    conexao = obter_bd()
    msg = conexao.execute("SELECT * FROM zap_mensagens WHERE id = ?", (mensagem_id,)).fetchone()
    if not msg:
        conexao.close()
        return jsonify({"ok": False})
    conexao.execute(
        "INSERT INTO zap_denuncias (mensagem_id, denunciante, criado_em) VALUES (?, ?, ?)",
        (mensagem_id, usuario, datetime.now().isoformat()),
    )
    conexao.commit()
    total_denuncias = conexao.execute("SELECT COUNT(*) AS n FROM zap_denuncias WHERE mensagem_id = ?", (mensagem_id,)).fetchone()["n"]
    if total_denuncias >= 3:
        aplicar_moderacao(msg["remetente"])
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/grupos")
def zap_grupos_pagina():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    return PAGINA_ZAP_GRUPOS


@app.route("/zap/grupos/lista")
def zap_grupos_lista():
    if not session.get("usuario"):
        return jsonify([]), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    linhas = conexao.execute(
        """SELECT g.* FROM zap_grupos g JOIN zap_grupo_membros m ON m.grupo_id = g.id
           WHERE m.usuario = ? COLLATE NOCASE ORDER BY g.criado_em DESC""",
        (usuario,),
    ).fetchall()
    conexao.close()
    return jsonify([{"id": l["id"], "nome": l["nome"], "foto": l["foto"], "verificado": bool(l["verificado"])} for l in linhas])


@app.route("/zap/grupos/criar", methods=["POST"])
def zap_grupos_criar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json() or {}
    nome = (dados.get("nome") or "").strip()
    membros = dados.get("membros") or []
    if not nome:
        return jsonify({"ok": False, "erro": "Digite um nome para o grupo."})
    conexao = obter_bd()
    cursor = conexao.execute(
        "INSERT INTO zap_grupos (nome, foto, criado_por, verificado, criado_em) VALUES (?, NULL, ?, 0, ?)",
        (nome, usuario, datetime.now().isoformat()),
    )
    grupo_id = cursor.lastrowid
    conexao.execute("INSERT OR IGNORE INTO zap_grupo_membros (grupo_id, usuario, admin) VALUES (?, ?, 1)", (grupo_id, usuario))
    for membro in membros:
        m = buscar_usuario(str(membro).strip())
        if m:
            conexao.execute("INSERT OR IGNORE INTO zap_grupo_membros (grupo_id, usuario) VALUES (?, ?)", (grupo_id, m["usuario"]))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "grupo_id": grupo_id})


def _membro_do_grupo(grupo_id, usuario):
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT 1 FROM zap_grupo_membros WHERE grupo_id = ? AND usuario = ? COLLATE NOCASE", (grupo_id, usuario)
    ).fetchone()
    conexao.close()
    return linha is not None


def _admin_do_grupo(grupo_id, usuario):
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT admin FROM zap_grupo_membros WHERE grupo_id = ? AND usuario = ? COLLATE NOCASE", (grupo_id, usuario)
    ).fetchone()
    conexao.close()
    return bool(linha and linha["admin"])


@app.route("/zap/grupo/<int:grupo_id>/info")
def zap_grupo_info(grupo_id):
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    if not _membro_do_grupo(grupo_id, usuario):
        return jsonify({"ok": False, "erro": "Voce nao faz parte deste grupo."}), 403
    conexao = obter_bd()
    grupo = conexao.execute("SELECT * FROM zap_grupos WHERE id = ?", (grupo_id,)).fetchone()
    if not grupo:
        conexao.close()
        return jsonify({"ok": False, "erro": "Grupo nao encontrado."}), 404
    membros = conexao.execute(
        """SELECT m.usuario, m.admin, u.foto_perfil FROM zap_grupo_membros m
           LEFT JOIN usuarios u ON u.usuario = m.usuario COLLATE NOCASE
           WHERE m.grupo_id = ? ORDER BY m.admin DESC, m.usuario ASC""",
        (grupo_id,),
    ).fetchall()
    conexao.close()
    return jsonify({
        "ok": True, "nome": grupo["nome"], "foto": grupo["foto"], "verificado": bool(grupo["verificado"]),
        "sou_admin": _admin_do_grupo(grupo_id, usuario),
        "membros": [{"usuario": m["usuario"], "admin": bool(m["admin"]), "foto": m["foto_perfil"]} for m in membros],
    })


@app.route("/zap/grupo/<int:grupo_id>/editar", methods=["POST"])
def zap_grupo_editar(grupo_id):
    """Edita nome e/ou foto do grupo. So um admin do grupo pode fazer isso."""
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    if not _admin_do_grupo(grupo_id, usuario):
        return jsonify({"ok": False, "erro": "So um admin do grupo pode editar."}), 403
    nome = (request.form.get("nome") or "").strip()
    foto = salvar_imagem(request.files.get("foto"))
    conexao = obter_bd()
    if nome:
        conexao.execute("UPDATE zap_grupos SET nome = ? WHERE id = ?", (nome, grupo_id))
    if foto:
        conexao.execute("UPDATE zap_grupos SET foto = ? WHERE id = ?", (foto, grupo_id))
    conexao.commit()
    grupo = conexao.execute("SELECT nome, foto FROM zap_grupos WHERE id = ?", (grupo_id,)).fetchone()
    conexao.close()
    return jsonify({"ok": True, "nome": grupo["nome"], "foto": grupo["foto"]})


@app.route("/zap/grupo/<int:grupo_id>/membro/adicionar", methods=["POST"])
def zap_grupo_membro_adicionar(grupo_id):
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    if not _admin_do_grupo(grupo_id, usuario):
        return jsonify({"ok": False, "erro": "So um admin do grupo pode adicionar membros."}), 403
    dados = request.get_json() or {}
    alvo = buscar_usuario((dados.get("usuario") or "").strip())
    if not alvo:
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    conexao = obter_bd()
    conexao.execute("INSERT OR IGNORE INTO zap_grupo_membros (grupo_id, usuario, admin) VALUES (?, ?, 0)", (grupo_id, alvo["usuario"]))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/grupo/<int:grupo_id>/membro/remover", methods=["POST"])
def zap_grupo_membro_remover(grupo_id):
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    if not _admin_do_grupo(grupo_id, usuario):
        return jsonify({"ok": False, "erro": "So um admin do grupo pode remover membros."}), 403
    dados = request.get_json() or {}
    alvo = (dados.get("usuario") or "").strip()
    if alvo.lower() == usuario.lower():
        return jsonify({"ok": False, "erro": "Use a opcao de sair do grupo para se remover."})
    conexao = obter_bd()
    conexao.execute("DELETE FROM zap_grupo_membros WHERE grupo_id = ? AND usuario = ? COLLATE NOCASE", (grupo_id, alvo))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/grupo/<int:grupo_id>/membro/promover", methods=["POST"])
def zap_grupo_membro_promover(grupo_id):
    """Torna um membro admin, ou tira o admin dele (alterna). So um admin pode fazer isso."""
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    if not _admin_do_grupo(grupo_id, usuario):
        return jsonify({"ok": False, "erro": "So um admin do grupo pode promover/rebaixar membros."}), 403
    dados = request.get_json() or {}
    alvo = (dados.get("usuario") or "").strip()
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT admin FROM zap_grupo_membros WHERE grupo_id = ? AND usuario = ? COLLATE NOCASE", (grupo_id, alvo)
    ).fetchone()
    if not linha:
        conexao.close()
        return jsonify({"ok": False, "erro": "Essa pessoa nao esta no grupo."})
    novo_estado = 0 if linha["admin"] else 1
    if linha["admin"] and novo_estado == 0:
        qtd_admins = conexao.execute("SELECT COUNT(*) AS n FROM zap_grupo_membros WHERE grupo_id = ? AND admin = 1", (grupo_id,)).fetchone()["n"]
        if qtd_admins <= 1:
            conexao.close()
            return jsonify({"ok": False, "erro": "O grupo precisa ter pelo menos um admin."})
    conexao.execute("UPDATE zap_grupo_membros SET admin = ? WHERE grupo_id = ? AND usuario = ? COLLATE NOCASE", (novo_estado, grupo_id, alvo))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "admin": bool(novo_estado)})


@app.route("/zap/grupo/<int:grupo_id>/sair", methods=["POST"])
def zap_grupo_sair(grupo_id):
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT admin FROM zap_grupo_membros WHERE grupo_id = ? AND usuario = ? COLLATE NOCASE", (grupo_id, usuario)
    ).fetchone()
    if not linha:
        conexao.close()
        return jsonify({"ok": False, "erro": "Voce nao faz parte deste grupo."})
    if linha["admin"]:
        qtd_admins = conexao.execute("SELECT COUNT(*) AS n FROM zap_grupo_membros WHERE grupo_id = ? AND admin = 1", (grupo_id,)).fetchone()["n"]
        qtd_membros = conexao.execute("SELECT COUNT(*) AS n FROM zap_grupo_membros WHERE grupo_id = ?", (grupo_id,)).fetchone()["n"]
        if qtd_admins <= 1 and qtd_membros > 1:
            conexao.close()
            return jsonify({"ok": False, "erro": "Torne outra pessoa admin antes de sair, o grupo precisa de pelo menos um."})
    conexao.execute("DELETE FROM zap_grupo_membros WHERE grupo_id = ? AND usuario = ? COLLATE NOCASE", (grupo_id, usuario))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/zap/grupo/<int:grupo_id>")
def zap_grupo_chat(grupo_id):
    if not session.get("usuario"):
        return redirect(url_for("login"))
    usuario = session["usuario"]
    if not _membro_do_grupo(grupo_id, usuario):
        return "Voce nao faz parte deste grupo", 403
    conexao = obter_bd()
    grupo = conexao.execute("SELECT * FROM zap_grupos WHERE id = ?", (grupo_id,)).fetchone()
    conexao.close()
    if not grupo:
        return "Grupo nao encontrado", 404
    selo = " " + selo_verificado_html(14) if grupo["verificado"] else ""
    pagina = PAGINA_ZAP_GRUPO_CHAT.replace("{nome_grupo}", grupo["nome"]).replace("{grupo_id}", str(grupo_id)).replace("{selo_dev_grupo}", selo)
    return pagina


@app.route("/zap/grupo/<int:grupo_id>/mensagens")
def zap_grupo_mensagens(grupo_id):
    if not session.get("usuario"):
        return jsonify({"mensagens": []}), 401
    usuario = session["usuario"]
    if not _membro_do_grupo(grupo_id, usuario):
        return jsonify({"mensagens": []}), 403
    conexao = obter_bd()
    linhas = conexao.execute(
        "SELECT * FROM zap_grupo_mensagens WHERE grupo_id = ? ORDER BY id ASC LIMIT 300", (grupo_id,)
    ).fetchall()
    conexao.close()
    return jsonify({"mensagens": [
        {"id": l["id"], "remetente": l["remetente"], "tipo": l["tipo"], "conteudo": l["conteudo"], "minha": l["remetente"].lower() == usuario.lower()}
        for l in linhas
    ]})


@app.route("/zap/grupo/<int:grupo_id>/enviar", methods=["POST"])
def zap_grupo_enviar(grupo_id):
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    if not _membro_do_grupo(grupo_id, usuario):
        return jsonify({"ok": False, "erro": "Voce nao faz parte deste grupo."}), 403
    dados = request.get_json() or {}
    texto = (dados.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False})
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO zap_grupo_mensagens (grupo_id, remetente, tipo, conteudo, criado_em) VALUES (?, ?, 'texto', ?, ?)",
        (grupo_id, usuario, texto, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/admin/zap/verificar_grupo", methods=["POST"])
def admin_zap_verificar_grupo():
    """Painel do dono: da (ou remove) o selo de verificado de um grupo."""
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    dados = request.get_json() or {}
    try:
        grupo_id = int(dados.get("grupo_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "erro": "ID de grupo invalido."})
    conexao = obter_bd()
    grupo = conexao.execute("SELECT verificado FROM zap_grupos WHERE id = ?", (grupo_id,)).fetchone()
    if not grupo:
        conexao.close()
        return jsonify({"ok": False, "erro": "Grupo nao encontrado."})
    novo_estado = 0 if grupo["verificado"] else 1
    conexao.execute("UPDATE zap_grupos SET verificado = ? WHERE id = ?", (novo_estado, grupo_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "verificado": bool(novo_estado)})


# ================= ROTAS DO CPA CODES =================

SISTEMA_EXTENSAO = (
    "Voce e o CPA Codes, um assistente de programacao. "
    "Gere codigo limpo e funcional (Python, Java, JavaScript, C# ou o que for pedido), sempre dentro de blocos "
    "de codigo cercados por crases triplas com o nome da linguagem (ex: ```python). "
    "Antes do bloco, explique em 1-2 frases curtas o que o codigo faz. Responda em portugues do Brasil."
)


@app.route("/extensao")
def extensao():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    marcar_atividade(session["usuario"])
    return PAGINA_EXTENSAO


@app.route("/extensao/chat", methods=["POST"])
def extensao_chat():
    if not session.get("usuario"):
        return jsonify({"resposta": "Sessao expirada, faca login novamente."}), 401
    if not _cliente:
        return jsonify({"resposta": "Chave da IA nao configurada no servidor."})
    dados = request.get_json() or {}
    mensagem = (dados.get("mensagem") or "").strip()
    linguagem = (dados.get("linguagem") or "qualquer linguagem apropriada").strip()
    if not mensagem:
        return jsonify({"resposta": "Descreva o que voce precisa que o codigo faca."})
    resposta = _cliente.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": SISTEMA_EXTENSAO},
            {"role": "user", "content": f"Linguagem preferida: {linguagem}.\nPedido: {mensagem}"},
        ],
        max_tokens=1200,
    )
    return jsonify({"resposta": resposta.choices[0].message.content})


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)
