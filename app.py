"""
Jarvis - Site Publico
Chat com IA + geracao de imagem + conversor + modo de voz + rede social (JarvisWEB) + suporte.
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

SISTEMA = (
    "Voce e o Jarvis, assistente de IA criado por Samuca. "
    "Responda em portugues do Brasil, de forma clara e amigavel, curto e direto (as vezes a resposta sera falada em voz alta, entao evite listas longas). "
    "Voce NAO tem controle sobre nenhum computador, e apenas um assistente de conversa e criacao de imagens."
)

CAMINHO_BD = os.environ.get("CAMINHO_BD", "jarvis.db")
CONTA_DESENVOLVEDOR = "SAMUCA"
PIN_VERIFICACAO = "9090"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
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
        ("id_publico", "INTEGER"), ("data_nascimento", "TEXT"),
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
    # ---------- JarvisZap ----------
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
    # ---------- Grupos do JarvisZap ----------
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_grupos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, foto TEXT,
            criado_por TEXT NOT NULL, verificado INTEGER DEFAULT 0, criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_grupo_membros (
            grupo_id INTEGER NOT NULL, usuario TEXT NOT NULL, PRIMARY KEY (grupo_id, usuario)
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS zap_grupo_mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, grupo_id INTEGER NOT NULL,
            remetente TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'texto', conteudo TEXT NOT NULL,
            criado_em TEXT NOT NULL
        )
    """)
    # ---------- Salvos (posts salvos pelo usuario no feed) ----------
    conexao.execute("CREATE TABLE IF NOT EXISTS salvos (usuario TEXT NOT NULL, post_id INTEGER NOT NULL, criado_em TEXT NOT NULL, PRIMARY KEY (usuario, post_id))")
    # ---------- Bloqueios individuais no JarvisZap (diferente do bloqueio global por moderacao) ----------
    conexao.execute("CREATE TABLE IF NOT EXISTS zap_bloqueios (usuario TEXT NOT NULL, bloqueado TEXT NOT NULL, criado_em TEXT NOT NULL, PRIMARY KEY (usuario, bloqueado))")
    # ---------- Configuracoes gerais (icones dos apps, selo customizado, etc) ----------
    conexao.execute("CREATE TABLE IF NOT EXISTS configuracoes (chave TEXT PRIMARY KEY, valor TEXT)")
    # ---------- Ligacoes de voz do JarvisZap (sinalizacao WebRTC via polling) ----------
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


def enviar_email_codigo(email, codigo):
    """Envia o codigo de login por email via SMTP. Se o SMTP nao estiver
    configurado (ambiente de teste/local), grava o codigo no console."""
    assunto = "Seu codigo de acesso - Jarvis"
    corpo = f"Seu codigo de verificacao e: {codigo}\n\nEle expira em {MINUTOS_VALIDADE_CODIGO} minutos.\nSe voce nao pediu esse codigo, ignore este email."
    if not (SMTP_HOST and SMTP_USUARIO and SMTP_SENHA):
        # SMTP nao configurado no servidor (variaveis de ambiente ausentes no Render).
        # Antes isso fingia sucesso e deixava a pessoa travada, porque o codigo so
        # aparecia no log do servidor, que ninguem alem do dono consegue ver.
        # Agora avisamos a rota chamadora para que o codigo seja mostrado na tela.
        print(f"[JARVIS] (SMTP nao configurado) codigo para {email}: {codigo}")
        return "sem_smtp"
    try:
        mensagem = MIMEText(corpo)
        mensagem["Subject"] = assunto
        mensagem["From"] = SMTP_REMETENTE
        mensagem["To"] = email
        contexto = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=contexto) as servidor:
            servidor.login(SMTP_USUARIO, SMTP_SENHA)
            servidor.sendmail(SMTP_REMETENTE, [email], mensagem.as_string())
        return True
    except Exception as erro:
        print(f"[JARVIS] falha ao enviar email para {email}: {erro}")
        return False


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


# ================= JarvisZap: presenca online, criptografia e moderacao =================

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
    """Identificador estavel (e igual dos dois lados) de uma conversa do JarvisZap entre duas contas."""
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


# Lista de termos usada pelo bot do Jarvis para barrar mensagens de texto com
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
html, body { height:100%; }
body { margin:0; font-family: 'Segoe UI', Arial, sans-serif; background:#000000; color:#f2f2f2; }
.tag-badge { display:inline-flex; align-items:center; gap:4px; font-size:10px; padding:2px 7px; border-radius:8px; font-weight:bold; color:#000; vertical-align:middle; margin-left:4px; }
.tag-badge img { width:12px; height:12px; border-radius:50%; object-fit:cover; }
"""

# ---------- SPLASH (tela de carregando ao entrar / cadastrar) ----------
PAGINA_CARREGANDO = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Jarvis</title>
<style>
""" + ESTILO_COMUM + """
body { height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; }
.logo-hud { width:110px; height:110px; position:relative; margin-bottom:24px; }
.anel { position:absolute; border-radius:50%; border:2px solid #ffffff; opacity:0.85; }
.anel1 { inset:0; animation: girar 6s linear infinite; }
.anel2 { inset:14px; border-color:#cccccc; animation: girar 4s linear infinite reverse; }
.anel3 { inset:30px; border-color:#999999; animation: girar 3s linear infinite; }
@keyframes girar { from { transform: rotate(0deg);} to { transform: rotate(360deg);} }
h1 { letter-spacing:4px; font-size:22px; margin:0; }
.credito { position:absolute; bottom:30px; color:#666666; font-size:12px; letter-spacing:1px; opacity:0.55; font-weight:300; }
</style></head>
<body>
<div class="logo-hud"><div class="anel anel1"></div><div class="anel anel2"></div><div class="anel anel3"></div></div>
<h1>JARVIS</h1>
<div class="credito">feito por samuca</div>
<script>setTimeout(() => { window.location.href = "/inicio"; }, 1800);</script>
</body></html>
"""

# ---------- LOGIN / CADASTRO (tela cheia: Google + email com codigo) ----------
PAGINA_LOGIN = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Jarvis</title>
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
<h2>Entrar no Jarvis</h2>
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
<title>Jarvis</title>
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
  <div class="relogio" id="relogio">--:--</div>
  <div class="data" id="dataAtual"></div>
  <div class="contadores">
    <div class="contador-item"><span class="pontinho-online"></span><span id="qtdOnline">{qtd_online}</span> online</div>
    <div class="contador-item"><span id="qtdContas">{qtd_contas}</span> contas</div>
  </div>
</div>
<div class="apps">
  <a class="app-icone" href="/rede"><div class="icone-quadrado">{icone_jarvisweb}</div>JarvisWEB</a>
  <a class="app-icone" href="/painel"><div class="icone-quadrado">{icone_jarvis}</div>Jarvis</a>
  <a class="app-icone" href="/zap"><div class="icone-quadrado">{icone_zap}</div>JarvisZap</a>
  <a class="app-icone" href="/extensao"><div class="icone-quadrado">&lt;/&gt;</div>Jarvis Extensao</a>
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
<title>Jarvis</title>
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
      <strong>Jarvis</strong>
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
      <span>JARVIS</span>
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
    const resposta = await fetch("/chat", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mensagem}) });
    const dados = await resposta.json();
    return dados.resposta;
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
    const resposta = await fetch("/imagem?prompt=" + encodeURIComponent(prompt));
    const dados = await resposta.json();
    carregando.remove();
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

# ---------- REDE (JarvisWEB) ----------
PAGINA_REDE = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>JarvisWEB</title>
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
.post-rodape { position:absolute; left:0; right:78px; bottom:0; padding:16px 14px calc(16px + env(safe-area-inset-bottom)); background:linear-gradient(transparent, #000000cc 70%); }
.post-cabecalho { display:flex; align-items:center; gap:8px; margin-bottom:6px; font-weight:bold; }
.post-cabecalho a { color:#f2f2f2; text-decoration:none; display:flex; align-items:center; gap:8px; }
.post-cabecalho img { width:34px; height:34px; border-radius:50%; object-fit:cover; border:1px solid #ffffff44; }
.post-texto { margin:4px 0 0; white-space:pre-wrap; font-size:13px; color:#eee; }
.acoes-laterais { position:absolute; right:10px; bottom:90px; display:flex; flex-direction:column; align-items:center; gap:20px; z-index:8; }
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
  <span class="titulo-topo">JarvisWEB</span>
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
  <a href="/painel" class="nav-item"><img class="nav-icone" src="{icone_jarvis_nav}"><span>Jarvis</span></a>
  <a href="/perfil/{usuario}" class="nav-item"><img class="nav-icone" src="{avatar_usuario_nav}"><span>Perfil</span></a>
</div>
<div class="lightbox" id="lightbox" onclick="fecharLightbox()">
  <span class="fechar-lightbox">&times;</span>
  <div id="lightboxConteudo"></div>
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
        else if (p.video) midiaHtml = "<div class='post-midia-wrap'><video controlsList='nodownload noremoteplayback' disablePictureInPicture oncontextmenu='return false' playsinline muted loop preload='metadata' src='" + p.video + "'></video></div>";
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
async function comentar(id) {
    const campo = document.getElementById("novoComent-" + id);
    const texto = campo.value.trim();
    if (!texto) return;
    await fetch("/rede/comentar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({post_id: id, texto: texto}) });
    campo.value = ""; carregarFeed();
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
<title>{nome_usuario} - JarvisWEB</title>
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
<title>Suporte - Jarvis</title>
<style>
""" + ESTILO_COMUM + """
body { height:100vh; display:flex; flex-direction:column; }
.topo { padding:14px 16px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; justify-content:space-between; gap:10px; }
.topo-esquerda { display:flex; align-items:center; gap:12px; }
.voltar { color:#ffffff; text-decoration:none; font-size:20px; }
.relogio-topo { font-size:13px; color:#888; }
.container { flex:1; display:flex; overflow:hidden; }
.lista-tickets { width:220px; border-right:1px solid #ffffff22; overflow-y:auto; padding:10px; flex-shrink:0; }
.item-ticket { padding:10px; border-radius:8px; margin-bottom:6px; background:#0d0d0d; border:1px solid #ffffff22; cursor:pointer; font-size:13px; }
.item-ticket:hover, .item-ticket.ativo { background:#1a1a1a; }
.chat-suporte { flex:1; display:flex; flex-direction:column; }
.mensagens-suporte { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:10px; }
.msg-s { max-width:75%; padding:10px 14px; border-radius:10px; font-size:14px; }
.msg-s.eu { align-self:flex-end; background:#1a1a1a; }
.msg-s.outro { align-self:flex-start; background:#0d0d0d; border:1px solid #ffffff22; }
.area-input-suporte { padding:14px 14px calc(14px + env(safe-area-inset-bottom)); border-top:1px solid #ffffff22; display:flex; gap:8px; }
.area-input-suporte input { flex:1; padding:12px; border-radius:8px; border:1px solid #ffffff33; background:#0d0d0d; color:#f2f2f2; }
.area-input-suporte button { padding:12px 16px; border-radius:8px; border:none; background:#ffffff; color:#000; font-weight:bold; cursor:pointer; }
.painel-admin { margin:12px 16px 0; background:#0d0d0d; border:1px solid #ffffff33; border-radius:12px; padding:14px; font-size:13px; }
.painel-admin input { padding:8px; border-radius:6px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; margin-right:6px; margin-top:6px; }
.painel-admin button { padding:8px 14px; border-radius:6px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; margin-top:6px; }
@media (max-width:720px) {
  .container { flex-direction:column; }
  .lista-tickets { width:100%; height:120px; border-right:none; border-bottom:1px solid #ffffff22; }
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
    mensagens.forEach(m => {
        const bolha = document.createElement("div");
        bolha.className = "msg-s " + (m.remetente === usuarioAtual ? "eu" : "outro");
        bolha.textContent = (m.remetente !== usuarioAtual ? m.remetente + ": " : "") + m.texto;
        div.appendChild(bolha);
    });
    div.scrollTop = div.scrollHeight;
}

async function carregarMeuTicket() {
    const resposta = await fetch("/suporte/meu_ticket");
    const dados = await resposta.json();
    ticketAtualId = dados.ticket_id;
    renderizarMensagensSuporte(dados.mensagens || []);
}

async function carregarListaTickets() {
    const resposta = await fetch("/suporte/tickets");
    const tickets = await resposta.json();
    const div = document.getElementById("listaTickets");
    div.style.display = "block";
    div.innerHTML = "";
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
    } else {
        await fetch("/suporte/enviar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({texto}) });
        carregarMeuTicket();
    }
}
document.getElementById("campoSuporte").addEventListener("keydown", e => { if (e.key === "Enter") enviarSuporte(); });

async function adicionarAgente() {
    const usuario = document.getElementById("nomeAgente").value.trim();
    const pin = document.getElementById("pinAgente").value.trim();
    const resposta = await fetch("/suporte/agente", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({usuario, pin}) });
    const dados = await resposta.json();
    document.getElementById("resultadoAgente").textContent = dados.ok ? "Atendente adicionado!" : (dados.erro || "Erro.");
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
<title>Jarvis</title><style>""" + ESTILO_COMUM + """
body { height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; padding:24px; }
.icone-bloqueio { font-size:48px; margin-bottom:16px; }
h2 { margin:0 0 10px; }
p { color:#999; max-width:340px; }
a { color:#fff; margin-top:18px; text-decoration:underline; }
</style></head><body>
<div class="icone-bloqueio">&#128683;</div>
<h2>Conta bloqueada no JarvisZap</h2>
<p>O bot do Jarvis identificou envios repetidos de conteudo proibido (pornografia, conteudo adulto ou conteudo de terror/ameaca) e bloqueou esta conta para o JarvisZap. Se acha que foi um engano, abra um chamado no Suporte.</p>
<a href="/suporte">Ir para o Suporte</a><br><a href="/inicio">Voltar ao inicio</a>
</body></html>
"""

# ---------- JARVISZAP ----------
PAGINA_ZAP = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>JarvisZap</title>
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
.topo-chat { padding:14px 18px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; gap:12px; }
.topo-chat img { width:34px; height:34px; border-radius:50%; object-fit:cover; }
.topo-chat-acoes { margin-left:auto; display:flex; gap:6px; flex-wrap:wrap; }
.badge-cripto { font-size:11px; padding:4px 10px; border-radius:12px; background:#0d0d0d; border:1px solid #3ddc6a55; color:#3ddc6a; display:none; }
.badge-cripto.ativo { display:inline-block; }
.msgs-zap { flex:1; overflow-y:auto; padding:18px; display:flex; flex-direction:column; gap:10px; }
.bolha { max-width:65%; padding:10px 14px; border-radius:12px; line-height:1.4; font-size:14px; position:relative; }
.bolha.minha { align-self:flex-end; background:#1f6feb33; border:1px solid #1f6feb55; }
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
}
.modal-chamada { display:none; position:fixed; inset:0; background:#000000f2; z-index:200; align-items:center; justify-content:center; flex-direction:column; color:#fff; text-align:center; }
.modal-chamada.aberto { display:flex; }
.modal-chamada img { width:96px; height:96px; border-radius:50%; object-fit:cover; margin-bottom:16px; border:2px solid #ffffff33; }
.modal-chamada .status-chamada { color:#888; margin-bottom:30px; font-size:14px; }
.modal-chamada .botoes-chamada { display:flex; gap:20px; }
.botao-chamada-circulo { width:56px; height:56px; border-radius:50%; border:none; font-size:22px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
.botao-chamada-circulo.aceitar { background:#3ddc6a; color:#000; }
.botao-chamada-circulo.recusar, .botao-chamada-circulo.encerrar { background:#ff3b3b; color:#fff; }
</style></head>
<body>
<div class="sidebar-zap" id="sidebarZap">
  <div class="topo-zap-lista"><a href="/inicio">&#8592;</a><b>JarvisZap</b><span class="btn-add-contato" onclick="menuAdicionar()">+</span></div>
  <div class="lista-contatos" id="listaContatos"><div class="vazio-contatos">Carregando...</div></div>
  <div style="padding:10px 14px;border-top:1px solid #ffffff1a;">
    <div class="item-contato" style="padding:8px 0;cursor:pointer;color:#888;" onclick="window.location.href='/zap/grupos'">&#128101; Meus grupos</div>
  </div>
</div>
<div class="chat-area">
  <div class="sem-conversa" id="semConversa">Adicione um contato pelo ID (#) para comecar a conversar.</div>
  <div id="conversaAberta" style="display:none; flex:1; display:flex; flex-direction:column; min-height:0;">
    <div class="topo-chat">
      <img id="avatarChatAtual" src="">
      <div><div id="nomeChatAtual" style="font-weight:bold;"></div><div id="idChatAtual" style="font-size:11px;color:#888;"></div></div>
      <div class="topo-chat-acoes">
        <span class="badge-cripto" id="badgeCripto" onclick="ativarCriptografia()" title="Toque para trocar a criptografia" style="display:none;">&#128274; criptografado</span>
        <span class="badge-cripto" id="botaoCriptografar" onclick="ativarCriptografia()" title="Ativar criptografia" style="display:inline-block;cursor:pointer;">&#128275; criptografar</span>
        <span class="badge-cripto" id="botaoBloquear" onclick="alternarBloqueio()" style="display:inline-block;cursor:pointer;border-color:#ff6b6b55;color:#ff6b6b;">Bloquear</span>
        <span class="badge-cripto" id="botaoLigar" onclick="iniciarChamada()" style="display:inline-block;cursor:pointer;border-color:#3ddc6a55;color:#3ddc6a;">&#128222; Ligar</span>
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
  <img id="avatarChamada" src="">
  <div id="nomeChamada" style="font-size:18px;font-weight:bold;"></div>
  <div class="status-chamada" id="statusChamada">Chamando...</div>
  <div class="botoes-chamada" id="botoesChamada"></div>
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
    if (!confirm("Denunciar esta mensagem para o bot do Jarvis analisar?")) return;
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

carregarContatos();
setInterval(() => { if (contatoAtual) carregarMensagens(); }, 4000);

// ---------- Ligacoes de voz (WebRTC + sinalizacao via polling) ----------
const CONFIG_ICE = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };
let pc = null, streamLocal = null, chamadaAtualId = null, souQuemLigou = false, contatoDaChamada = null;
let indiceCandidatosRecebidos = 0, pollCandidatos = null, pollStatusLigacao = null, pollChamadaEntrando = null;

let mutadoLocal = false;
function botoesEmChamadaHtml() {
    return '<button class="botao-chamada-circulo" id="botaoMudo" style="background:#333;color:#fff;" onclick="alternarMudo()">' + (mutadoLocal ? '&#128263;' : '&#127908;') + '</button>' +
           '<button class="botao-chamada-circulo encerrar" onclick="encerrarChamada(true)">&#128222;</button>';
}
function alternarMudo() {
    if (!streamLocal) return;
    mutadoLocal = !mutadoLocal;
    streamLocal.getAudioTracks().forEach(t => t.enabled = !mutadoLocal); // so o MEU audio, nao mexe no do outro lado
    const botao = document.getElementById("botaoMudo");
    if (botao) botao.innerHTML = mutadoLocal ? "&#128263;" : "&#127908;";
}

function abrirModalChamada(nome, avatar, statusTexto, botoesHtml) {
    document.getElementById("nomeChamada").textContent = nome;
    document.getElementById("avatarChamada").src = avatar || (contatos.find(c => c.usuario === nome) || {}).avatar || "";
    document.getElementById("statusChamada").textContent = statusTexto;
    document.getElementById("botoesChamada").innerHTML = botoesHtml;
    document.getElementById("modalChamada").classList.add("aberto");
}
function fecharModalChamada() { document.getElementById("modalChamada").classList.remove("aberto"); }

async function criarConexao(alvoNome) {
    pc = new RTCPeerConnection(CONFIG_ICE);
    streamLocal = await navigator.mediaDevices.getUserMedia({ audio: true });
    streamLocal.getTracks().forEach(t => pc.addTrack(t, streamLocal));
    pc.ontrack = (ev) => { document.getElementById("audioRemoto").srcObject = ev.streams[0]; };
    pc.onicecandidate = (ev) => {
        if (ev.candidate && chamadaAtualId) {
            fetch("/zap/chamada/candidato", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({chamada_id: chamadaAtualId, candidato: ev.candidate}) });
        }
    };
    pc.onconnectionstatechange = () => {
        if (pc && (pc.connectionState === "disconnected" || pc.connectionState === "failed" || pc.connectionState === "closed")) encerrarChamada(false);
    };
}

function iniciarPollCandidatos() {
    indiceCandidatosRecebidos = 0;
    pollCandidatos = setInterval(async () => {
        if (!chamadaAtualId || !pc) return;
        const r = await fetch("/zap/chamada/candidatos/" + chamadaAtualId + "?desde=" + indiceCandidatosRecebidos);
        const d = await r.json();
        for (const c of d.candidatos) { try { await pc.addIceCandidate(c); } catch (e) {} }
        indiceCandidatosRecebidos += d.candidatos.length;
        if (d.status === "encerrada" || d.status === "recusada") encerrarChamada(false);
    }, 1500);
}

async function iniciarChamada() {
    if (!contatoAtual) return;
    contatoDaChamada = contatoAtual;
    souQuemLigou = true;
    abrirModalChamada(contatoDaChamada, null, "Chamando...", '<button class="botao-chamada-circulo encerrar" onclick="encerrarChamada(true)">&#128222;</button>');
    await criarConexao(contatoDaChamada);
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
    abrirModalChamada(contatoDaChamada, null, "Chamada recebida...",
        '<button class="botao-chamada-circulo aceitar" onclick="aceitarChamada()">&#9742;</button>' +
        '<button class="botao-chamada-circulo recusar" onclick="recusarChamada()">&#10006;</button>');
}

async function aceitarChamada() {
    await criarConexao(contatoDaChamada);
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

# ---------- GRUPOS DO JARVISZAP ----------
PAGINA_ZAP_GRUPOS = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Grupos - JarvisZap</title>
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
<div class="topo"><a href="/zap">&#8592;</a>Grupos do JarvisZap</div>
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
<title>{nome_grupo} - JarvisZap</title>
<style>
""" + ESTILO_COMUM + """
body { display:flex; flex-direction:column; height:100vh; }
.topo-chat { padding:14px 18px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; gap:12px; }
.topo-chat a { color:#fff; text-decoration:none; font-size:18px; }
.msgs-zap { flex:1; overflow-y:auto; padding:18px; display:flex; flex-direction:column; gap:10px; }
.bolha { max-width:70%; padding:10px 14px; border-radius:12px; line-height:1.4; font-size:14px; }
.bolha.minha { align-self:flex-end; background:#1f6feb33; border:1px solid #1f6feb55; }
.bolha.dele { align-self:flex-start; background:#0d0d0d; border:1px solid #ffffff22; }
.bolha .remetente { font-size:11px; color:#888; margin-bottom:2px; }
.area-input-zap { padding:12px 16px calc(12px + env(safe-area-inset-bottom)); border-top:1px solid #ffffff22; display:flex; gap:8px; }
.area-input-zap input[type=text] { flex:1; padding:12px 14px; border-radius:20px; border:1px solid #ffffff33; background:#0d0d0d; color:#f2f2f2; }
.area-input-zap button { background:#fff; color:#000; border:none; border-radius:50%; width:40px; height:40px; cursor:pointer; }
</style></head>
<body>
<div class="topo-chat"><a href="/zap/grupos">&#8592;</a><b>{nome_grupo}</b>{selo_dev_grupo}</div>
<div class="msgs-zap" id="msgsGrupo"></div>
<div class="area-input-zap">
  <input type="text" id="campoGrupo" placeholder="Mensagem" onkeydown="if(event.key==='Enter')enviarMsgGrupo()">
  <button onclick="enviarMsgGrupo()">&#10148;</button>
</div>
<script>
const grupoId = {grupo_id};
function escaparHtml(t) { const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
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

# ---------- JARVIS EXTENSAO (chat focado em gerar codigo) ----------
PAGINA_EXTENSAO = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis Extensao</title>
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
<div class="topo-ext"><a href="/inicio">&#8592;</a><b>Jarvis Extensao</b>
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
    resultado = enviar_email_codigo(email, codigo)
    if resultado is False:
        return jsonify({"ok": False, "erro": "Nao foi possivel enviar o codigo. Tente novamente."})
    resposta = {"ok": True}
    if resultado == "sem_smtp":
        # Servidor sem SMTP configurado: manda o codigo junto da resposta para
        # a pessoa nao ficar travada na tela de login.
        resposta["codigo_teste"] = codigo
    return jsonify(resposta)


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
    return PAGINA_CARREGANDO


@app.route("/inicio")
def inicio():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    marcar_atividade(session["usuario"])
    pagina = PAGINA_INICIO.replace("{fundo_url}", FUNDO_INICIO_URL)
    pagina = pagina.replace("{qtd_online}", str(contar_online()))
    pagina = pagina.replace("{qtd_contas}", str(contar_contas()))

    def icone_img(chave, letra):
        url = obter_config(chave)
        return f'<img src="{url}">' if url else letra

    pagina = pagina.replace("{icone_jarvisweb}", icone_img("icone_jarvisweb", "W"))
    pagina = pagina.replace("{icone_jarvis}", icone_img("icone_jarvis", "J"))
    pagina = pagina.replace("{icone_zap}", icone_img("icone_zap", "Z"))
    pagina = pagina.replace("{icone_suporte}", icone_img("icone_suporte", "S"))
    pagina = pagina.replace("{icone_app_url}", obter_config("icone_app", AVATAR_PADRAO + "jarvisapp"))
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
    icone = obter_config("icone_app", AVATAR_PADRAO + "jarvisapp")
    manifest = {
        "name": "Jarvis",
        "short_name": "Jarvis",
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
<title>Baixar o Jarvis</title>
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
<h2>Instalar o Jarvis</h2>
<p>Instale o Jarvis na tela inicial do seu celular pra abrir como um app, sem precisar do navegador.</p>
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
    icone = obter_config("icone_app", AVATAR_PADRAO + "jarvisapp")
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
    if not _cliente:
        return jsonify({"resposta": "Chave da IA nao configurada no servidor."})
    dados = request.get_json()
    mensagem = dados.get("mensagem", "").strip()
    if not mensagem:
        return jsonify({"resposta": "Nao recebi nenhuma mensagem."})
    usuario = session["usuario"]
    conexao = obter_bd()
    conexao.execute("INSERT INTO mensagens (usuario, remetente, texto, criado_em) VALUES (?, ?, ?, ?)", (usuario, "usuario", mensagem, datetime.now().isoformat()))
    linhas = conexao.execute("SELECT remetente, texto FROM mensagens WHERE usuario = ? ORDER BY id DESC LIMIT 12", (usuario,)).fetchall()
    historico_mensagens = [{"role": "user" if l["remetente"] == "usuario" else "assistant", "content": l["texto"]} for l in reversed(linhas)]
    resposta = _cliente.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": SISTEMA}] + historico_mensagens,
        max_tokens=500,
    )
    texto_resposta = resposta.choices[0].message.content
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
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt_melhorado) + f"?model=flux&width=1024&height=1024&seed={seed}&nologo=true"
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
              <button onclick="mudarAbaAdmin('zap', this)">JarvisZap</button>
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
                <div class="item-icone"><img id="previaIconeJarvis" src="{icone_jarvis}"><input type="file" accept="image/*" id="arqIconeJarvis" style="display:none" onchange="enviarConfig('icone_jarvis','arqIconeJarvis','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeJarvis').click()">Jarvis</button></div>
                <div class="item-icone"><img id="previaIconeJarvisweb" src="{icone_jarvisweb}"><input type="file" accept="image/*" id="arqIconeJarvisweb" style="display:none" onchange="enviarConfig('icone_jarvisweb','arqIconeJarvisweb','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeJarvisweb').click()">JarvisWEB</button></div>
                <div class="item-icone"><img id="previaIconeZap" src="{icone_zap}"><input type="file" accept="image/*" id="arqIconeZap" style="display:none" onchange="enviarConfig('icone_zap','arqIconeZap','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeZap').click()">JarvisZap</button></div>
                <div class="item-icone"><img id="previaIconeSuporte" src="{icone_suporte}"><input type="file" accept="image/*" id="arqIconeSuporte" style="display:none" onchange="enviarConfig('icone_suporte','arqIconeSuporte','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeSuporte').click()">Suporte</button></div>
                <div class="item-icone"><img id="previaIconeApp" src="{icone_app}"><input type="file" accept="image/*" id="arqIconeApp" style="display:none" onchange="enviarConfig('icone_app','arqIconeApp','resultadoIcones',this)"><button class="acao" style="padding:4px 8px;font-size:10px;" onclick="document.getElementById('arqIconeApp').click()">Icone do app (instalar)</button></div>
              </div>
              <label class="rotulo-campo">Logo da tela de login/splash</label>
              <div class="linha-admin">
                <input id="logoArquivo" type="file" accept="image/*">
                <button class="acao" onclick="enviarConfig('logo_login','logoArquivo','resultadoLogo')">Salvar logo</button>
              </div>
              <div class="resultado-admin" id="resultadoLogo"></div>
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
            .replace("{icone_app}", obter_config("icone_app", AVATAR_PADRAO + "jarvisapp"))
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
        <input id="nomeAgente" placeholder="usuario"><input id="pinAgente" placeholder="PIN" type="password">
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
    dados = request.get_json()
    alvo = dados.get("usuario", "").strip()
    pin = dados.get("pin", "").strip()
    if pin != PIN_VERIFICACAO:
        return jsonify({"ok": False, "erro": "PIN incorreto."})
    conexao = obter_bd()
    existe = conexao.execute("SELECT 1 FROM usuarios WHERE usuario = ? COLLATE NOCASE", (alvo,)).fetchone()
    if not existe:
        conexao.close()
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    conexao.execute("INSERT OR IGNORE INTO agentes_suporte (usuario) VALUES (?)", (alvo,))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


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


# ================= ROTAS DO JARVISZAP =================

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
    aviso = f"&#128274; {usuario} ativou a criptografia desta conversa. O bot do Jarvis vai cifrar as mensagens de texto a partir de agora."
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
        return jsonify({"ok": False, "bloqueado": True, "erro": "Sua conta esta bloqueada no JarvisZap."})
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
            aviso = ("O bot do Jarvis bloqueou esta mensagem por conter conteudo proibido (adulto/+18 ou de terror/ameaca). "
                     f"Aviso {avisos}/3." + (" Conta bloqueada no JarvisZap." if bloqueado else ""))
            conexao.execute(
                "INSERT INTO zap_mensagens (conversa, remetente, destinatario, tipo, conteudo, criptografado, criado_em) VALUES (?, 'jarvis', ?, 'sistema', ?, 0, ?)",
                (conversa, conversa, aviso, datetime.now().isoformat()),
            )
            conexao.commit()
            conexao.close()
            return jsonify({"ok": False, "erro": "Mensagem bloqueada pelo bot do Jarvis.", "bloqueado": bloqueado})

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
        return jsonify({"ok": False, "bloqueado": True, "erro": "Sua conta esta bloqueada no JarvisZap."})
    contato = (request.form.get("contato") or "").strip()
    tipo = request.form.get("tipo", "imagem")
    alvo = buscar_usuario(contato)
    arquivo = request.files.get("arquivo")
    if not alvo or not arquivo:
        return jsonify({"ok": False, "erro": "Dados invalidos."})
    if usuario_bloqueou(usuario, alvo["usuario"]):
        return jsonify({"ok": False, "erro": "Voce nao pode enviar mensagem para este contato (bloqueado)."})
    # Aviso: o bot do Jarvis ainda nao analisa o CONTEUDO de imagens/audios/videos
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
    conexao.execute("INSERT OR IGNORE INTO zap_grupo_membros (grupo_id, usuario) VALUES (?, ?)", (grupo_id, usuario))
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


# ================= ROTAS DO JARVIS EXTENSAO =================

SISTEMA_EXTENSAO = (
    "Voce e o Jarvis em Modo Extensao, um assistente de programacao. "
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
        model="llama-3.3-70b-versatile",
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
