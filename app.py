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

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "troque_essa_chave_em_producao")

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
    """Gera um ID permanente aleatorio, unico, sempre acima de 10 (1 e reservado ao dono)."""
    while True:
        candidato = random.randint(11, 999999)
        existe = conexao.execute("SELECT 1 FROM usuarios WHERE id_publico = ?", (candidato,)).fetchone()
        if not existe:
            return candidato


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


ICONE_MIC = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 14a3 3 0 003-3V6a3 3 0 00-6 0v5a3 3 0 003 3zm5-3a5 5 0 01-10 0H5a7 7 0 006 6.92V21h2v-3.08A7 7 0 0019 11h-2z"/></svg>"""
ICONE_MIC_OFF = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 11h-2a5 5 0 01-8.11 3.91L7.5 16.3A7 7 0 0017 11h2zM4.27 3L3 4.27l6 6V11a3 3 0 003 3c.2 0 .38-.03.56-.06l1.55 1.55A5 5 0 0112 9v.73L19.73 21 21 19.73 4.27 3z"/></svg>"""
ICONE_ONDA = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M3 10v4h3l4 4V6l-4 4H3zm10.5 2a4.5 4.5 0 00-2.5-4.03v8.06A4.5 4.5 0 0013.5 12zM13 3.23v2.06c3.39.87 5.5 4.24 4.63 7.63A6.98 6.98 0 0113 17.71v2.06c4.5-.93 7.44-5.33 6.51-9.83A8.02 8.02 0 0013 3.23z"/></svg>"""
SELO_VERIFICADO = """<svg viewBox="0 0 24 24" width="14" height="14" fill="#fff" style="vertical-align:middle;margin-left:3px;"><path d="M12 2l2.4 2.4 3.3-.5.8 3.3 3.1 1.4-1.1 3.2 1.1 3.2-3.1 1.4-.8 3.3-3.3-.5L12 22l-2.4-2.4-3.3.5-.8-3.3-3.1-1.4 1.1-3.2-1.1-3.2 3.1-1.4.8-3.3 3.3.5z"/><path d="M9.5 12.5l1.8 1.8 3.2-4" stroke="#000" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
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
<img src="/static/logo.jpg" class="logo-img" onerror="this.style.display='none'">
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
<style>
""" + ESTILO_COMUM + """
body { height:100vh; display:flex; flex-direction:column; }
.status-topo { text-align:center; padding:30px 0 10px; }
.relogio { font-size:44px; font-weight:200; letter-spacing:2px; }
.data { font-size:13px; color:#888; margin-top:4px; }
.apps { flex:1; display:flex; align-items:center; justify-content:center; gap:36px; flex-wrap:wrap; padding:20px; }
.app-icone { display:flex; flex-direction:column; align-items:center; gap:10px; cursor:pointer; text-decoration:none; color:#f2f2f2; }
.icone-quadrado { width:64px; height:64px; border-radius:18px; background:#0d0d0d; border:1px solid #ffffff22; display:flex; align-items:center; justify-content:center; font-size:26px; }
.rodape { text-align:center; padding:16px; font-size:12px; color:#666; }
.sair-link { color:#888; text-decoration:underline; cursor:pointer; }
@media (max-width:480px) { .relogio { font-size:36px; } .apps { gap:24px; } }
</style></head>
<body>
<div class="status-topo">
  <div class="relogio" id="relogio">--:--</div>
  <div class="data" id="dataAtual"></div>
</div>
<div class="apps">
  <a class="app-icone" href="/rede"><div class="icone-quadrado">W</div>JarvisWEB</a>
  <a class="app-icone" href="/painel"><div class="icone-quadrado">J</div>Jarvis</a>
  <a class="app-icone" href="/suporte"><div class="icone-quadrado">S</div>Suporte</a>
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
.area-input { padding:16px; border-top:1px solid #ffffff22; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
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
  <a class="link-rede" href="/rede">JarvisWEB</a>
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
body { height:100vh; overflow-y:auto; }
.topo { position:sticky; top:0; background:#000000; padding:14px 16px; border-bottom:1px solid #ffffff22; display:flex; align-items:center; gap:12px; z-index:5; }
.voltar { color:#ffffff; text-decoration:none; font-size:20px; }
.titulo-topo { font-weight:bold; letter-spacing:1px; }
.container { max-width:600px; margin:0 auto; padding:16px; }
.caixa-postar { background:#0d0d0d; border:1px solid #ffffff22; border-radius:12px; padding:14px; margin-bottom:20px; }
.caixa-postar textarea { width:100%; background:#000000; border:1px solid #ffffff22; border-radius:8px; color:#f2f2f2; padding:10px; resize:vertical; min-height:60px; }
.caixa-postar input { width:100%; margin-top:8px; padding:8px; border-radius:6px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; font-size:12px; }
.caixa-postar button { margin-top:10px; padding:10px 18px; border-radius:8px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; }
.post { background:#0d0d0d; border:1px solid #ffffff22; border-radius:12px; padding:14px; margin-bottom:16px; }
.post-cabecalho { display:flex; align-items:center; gap:8px; margin-bottom:8px; font-weight:bold; }
.post-cabecalho a { color:#f2f2f2; text-decoration:none; display:flex; align-items:center; gap:8px; }
.post-cabecalho img { width:32px; height:32px; border-radius:50%; object-fit:cover; }
.post-texto { margin:8px 0; white-space:pre-wrap; }
.post img.post-imagem, .post video { max-width:100%; border-radius:8px; margin-top:6px; cursor:pointer; }
.post-acoes { display:flex; gap:16px; margin-top:10px; font-size:13px; }
.post-acoes span { cursor:pointer; color:#cccccc; }
.post-acoes span.ativo { color:#ffffff; font-weight:bold; }
.comentarios { margin-top:10px; border-top:1px solid #ffffff22; padding-top:8px; font-size:13px; }
.comentario { margin-bottom:6px; }
.comentario b { color:#ffffff; }
.caixa-comentar { display:flex; gap:6px; margin-top:6px; }
.caixa-comentar input { flex:1; padding:8px; border-radius:6px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; font-size:12px; }
.caixa-comentar button { padding:8px 12px; border-radius:6px; border:none; background:#1a1a1a; color:#f2f2f2; cursor:pointer; font-size:12px; }
.painel-admin { background:#0d0d0d; border:1px solid #ffffff33; border-radius:12px; padding:14px; margin-bottom:20px; font-size:13px; }
.painel-admin input, .painel-admin input[type=color] { padding:8px; border-radius:6px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; margin-right:6px; margin-top:6px; }
.painel-admin button { padding:8px 14px; border-radius:6px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; margin-top:6px; }
.painel-admin hr { border-color:#ffffff22; margin:12px 0; }
.lightbox { display:none; position:fixed; inset:0; background:#000000ee; z-index:100; align-items:center; justify-content:center; padding:16px; }
.lightbox.aberto { display:flex; }
.lightbox img, .lightbox video { max-width:92vw; max-height:88vh; border-radius:10px; }
.fechar-lightbox { position:absolute; top:20px; right:20px; color:#fff; font-size:28px; cursor:pointer; }
@media (max-width:480px) { .container { padding:10px; } }
</style></head>
<body>
<div class="topo"><a href="/inicio" class="voltar">&#8592;</a><span class="titulo-topo">JarvisWEB</span></div>
<div class="container">
  {painel_admin}
  <div class="caixa-postar">
    <textarea id="textoPost" placeholder="No que voce esta pensando?"></textarea>
    <input type="file" id="imagemPost" accept="image/*">
    <input type="text" id="linkImagemPost" placeholder="Link de imagem (opcional, se nao for enviar arquivo)">
    <input type="text" id="videoPost" placeholder="Link de video (Discord ou outro, opcional)">
    <br><button onclick="publicar()">Postar</button>
  </div>
  <div id="feed"></div>
</div>
<div class="lightbox" id="lightbox" onclick="fecharLightbox()">
  <span class="fechar-lightbox">&times;</span>
  <div id="lightboxConteudo"></div>
</div>
<script>
const usuarioLogado = "{usuario}";
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
async function carregarFeed() {
    const resposta = await fetch("/rede/feed");
    const posts = await resposta.json();
    const div = document.getElementById("feed");
    div.innerHTML = "";
    posts.forEach(p => {
        const bloco = document.createElement("div");
        bloco.className = "post";
        const selo = p.verificado ? ' """ + SELO_VERIFICADO.replace('"', "'") + """' : '';
        let html = '<div class="post-cabecalho"><a href="/perfil/' + p.usuario + '"><img src="' + p.avatar + '">' + p.usuario + selo + (p.tag_html || '') + '</a></div>';
        if (p.texto) html += '<div class="post-texto"></div>';
        if (p.imagem) html += "<img class='post-imagem' src='" + p.imagem + "' onclick=\\"abrirLightbox('" + p.imagem + "','imagem',event)\\">";
        if (p.video) html += "<video controls src='" + p.video + "' onclick=\\"abrirLightbox('" + p.video + "','video',event)\\"></video>";
        html += '<div class="post-acoes">';
        html += '<span class="' + (p.curtido ? 'ativo' : '') + '" onclick="curtir(' + p.id + ')">Curtir (' + p.curtidas + ')</span>';
        html += '<span onclick="mostrarComentarios(' + p.id + ')">Comentar (' + p.comentarios.length + ')</span>';
        if (p.usuario !== usuarioLogado) {
            html += '<span class="' + (p.seguindo ? 'ativo' : '') + '" onclick="seguir(\\'' + p.usuario + '\\')">' + (p.seguindo ? 'Seguindo' : 'Seguir') + '</span>';
        }
        html += '</div><div class="comentarios" id="coment-' + p.id + '" style="display:none;">';
        p.comentarios.forEach(c => { html += '<div class="comentario"><b>' + c.usuario + ':</b> </div>'; });
        html += '<div class="caixa-comentar"><input id="novoComent-' + p.id + '" placeholder="Comentar..."><button onclick="comentar(' + p.id + ')">Enviar</button></div></div>';
        bloco.innerHTML = html;
        if (p.texto) bloco.querySelector(".post-texto").textContent = p.texto;
        bloco.querySelectorAll(".comentario").forEach((elemento, i) => { elemento.querySelector("b").nextSibling.textContent = " " + p.comentarios[i].texto; });
        div.appendChild(bloco);
    });
}
function mostrarComentarios(id) { const el = document.getElementById("coment-" + id); el.style.display = el.style.display === "none" ? "block" : "none"; }
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
async function verificar() {
    const alvo = document.getElementById("alvoVerificar").value.trim();
    const pin = document.getElementById("pinVerificar").value.trim();
    const resultado = document.getElementById("resultadoVerificar");
    const resposta = await fetch("/rede/verificar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo: alvo, pin: pin}) });
    const dados = await resposta.json();
    resultado.textContent = dados.ok ? "Verificado com sucesso!" : (dados.erro || "Erro.");
    if (dados.ok) carregarFeed();
}
async function criarTag() {
    const form = new FormData();
    form.append("nome", document.getElementById("tagNome").value.trim());
    form.append("cor", document.getElementById("tagCor").value);
    form.append("pin", document.getElementById("tagPin").value.trim());
    const arquivo = document.getElementById("tagFoto").files[0];
    if (arquivo) form.append("foto", arquivo);
    const resposta = await fetch("/rede/criar_tag", { method: "POST", body: form });
    const dados = await resposta.json();
    document.getElementById("resultadoTag").textContent = dados.ok ? "Tag criada!" : (dados.erro || "Erro.");
}
async function atribuirTag() {
    const alvo = document.getElementById("tagAlvo").value.trim();
    const tag = document.getElementById("tagNomeAtribuir").value.trim();
    const pin = document.getElementById("tagPinAtribuir").value.trim();
    const resposta = await fetch("/rede/atribuir_tag", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo, tag, pin}) });
    const dados = await resposta.json();
    document.getElementById("resultadoAtribuir").textContent = dados.ok ? "Tag atribuida!" : (dados.erro || "Erro.");
    if (dados.ok) carregarFeed();
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
.area-input-suporte { padding:14px; border-top:1px solid #ffffff22; display:flex; gap:8px; }
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
    return PAGINA_LOGIN.replace("{bloco_google}", bloco_google)


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
    if id_publico is None or (id_publico == 1 and not eh_dono) or (id_publico != 1 and id_publico <= 10):
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
    return PAGINA_INICIO


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
    if eh_dev:
        painel_admin_html = """
        <div class="painel-admin"><b>Painel do desenvolvedor</b><br>
        Dar selo verificado:<br>
        <input id="alvoVerificar" placeholder="usuario"><input id="pinVerificar" placeholder="PIN" type="password">
        <button onclick="verificar()">Verificar</button>
        <div id="resultadoVerificar" style="margin-top:6px;color:#ffffff;"></div>
        <hr>
        Criar tag (nome, cor e foto):<br>
        <input id="tagNome" placeholder="nome da tag"><input id="tagCor" type="color" value="#ffffff">
        <input id="tagFoto" type="file" accept="image/*"><input id="tagPin" placeholder="PIN" type="password">
        <button onclick="criarTag()">Criar tag</button>
        <div id="resultadoTag" style="margin-top:6px;color:#ffffff;"></div>
        <hr>
        Atribuir tag a alguem:<br>
        <input id="tagAlvo" placeholder="usuario"><input id="tagNomeAtribuir" placeholder="nome da tag">
        <input id="tagPinAtribuir" placeholder="PIN" type="password">
        <button onclick="atribuirTag()">Atribuir</button>
        <div id="resultadoAtribuir" style="margin-top:6px;color:#ffffff;"></div>
        </div>
        """
    return PAGINA_REDE.replace("{usuario}", usuario).replace("{painel_admin}", painel_admin_html)


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
    selo = SELO_VERIFICADO if linha_alvo["verificado"] else ""
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
    if novo_id != 1 and novo_id <= 10:
        return jsonify({"ok": False, "erro": "O ID precisa ser maior que 10."})
    conexao = obter_bd()
    em_uso = conexao.execute("SELECT 1 FROM usuarios WHERE id_publico = ? AND usuario != ?", (novo_id, usuario)).fetchone()
    if em_uso:
        conexao.close()
        return jsonify({"ok": False, "erro": "Esse ID ja esta em uso."})
    conexao.execute("UPDATE usuarios SET id_publico = ? WHERE usuario = ?", (novo_id, usuario))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "id_publico": novo_id})


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
        linha_autor = conexao.execute("SELECT verificado, foto_perfil, tag FROM usuarios WHERE usuario = ?", (p["usuario"],)).fetchone()
        verificado = bool(linha_autor and linha_autor["verificado"])
        avatar = (linha_autor["foto_perfil"] if linha_autor and linha_autor["foto_perfil"] else AVATAR_PADRAO + p["usuario"])
        tag_html = html_tag(linha_autor["tag"] if linha_autor else None)
        resultado.append({
            "id": p["id"], "usuario": p["usuario"], "texto": p["texto"], "imagem": p["imagem"], "video": p["video"],
            "avatar": avatar, "curtidas": curtidas, "curtido": curtido, "seguindo": seguindo, "verificado": verificado,
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
    alvo = dados.get("alvo", "").strip()
    pin = dados.get("pin", "").strip()
    if pin != PIN_VERIFICACAO:
        return jsonify({"ok": False, "erro": "PIN incorreto."})
    conexao = obter_bd()
    existe = conexao.execute("SELECT 1 FROM usuarios WHERE usuario = ? COLLATE NOCASE", (alvo,)).fetchone()
    if not existe:
        conexao.close()
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    conexao.execute("UPDATE usuarios SET verificado = 1 WHERE usuario = ? COLLATE NOCASE", (alvo,))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/rede/criar_tag", methods=["POST"])
def rede_criar_tag():
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    nome = request.form.get("nome", "").strip()
    cor = request.form.get("cor", "#ffffff").strip()
    pin = request.form.get("pin", "").strip()
    if pin != PIN_VERIFICACAO:
        return jsonify({"ok": False, "erro": "PIN incorreto."})
    if not nome:
        return jsonify({"ok": False, "erro": "Nome obrigatorio."})
    foto = salvar_imagem(request.files.get("foto"))
    conexao = obter_bd()
    existente = conexao.execute("SELECT foto FROM tags WHERE nome = ?", (nome,)).fetchone()
    if not foto and existente:
        foto = existente["foto"]
    conexao.execute("INSERT OR REPLACE INTO tags (nome, cor, foto) VALUES (?, ?, ?)", (nome, cor, foto))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/rede/atribuir_tag", methods=["POST"])
def rede_atribuir_tag():
    if not eh_desenvolvedor(session.get("usuario")):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    dados = request.get_json()
    alvo = dados.get("alvo", "").strip()
    tag = dados.get("tag", "").strip()
    pin = dados.get("pin", "").strip()
    if pin != PIN_VERIFICACAO:
        return jsonify({"ok": False, "erro": "PIN incorreto."})
    conexao = obter_bd()
    existe = conexao.execute("SELECT 1 FROM usuarios WHERE usuario = ? COLLATE NOCASE", (alvo,)).fetchone()
    if not existe:
        conexao.close()
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    conexao.execute("UPDATE usuarios SET tag = ? WHERE usuario = ? COLLATE NOCASE", (tag or None, alvo))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


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


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)
