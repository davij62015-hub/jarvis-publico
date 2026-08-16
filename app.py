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
# ---------- BOAS-VINDAS CINEMATOGRAFICA (so na primeira vez que a pessoa entra) ----------
PAGINA_BOAS_VINDAS = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JARVIS IA</title>
<style>
  :root{
    --bg:#03060c;
    --cyan:#5be6ff;
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
