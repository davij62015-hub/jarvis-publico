"""
Jarvis - Site Publico
Chat com IA + geracao de imagem + conversor + modo de voz + rede social basica (JarvisWEB).
NAO tem nenhum comando de controle de PC.
"""

import os
import io
import sqlite3
import urllib.parse
import random
import uuid
from datetime import datetime
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
PASTA_UPLOADS = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(PASTA_UPLOADS, exist_ok=True)


def obter_bd():
    conexao = sqlite3.connect(CAMINHO_BD)
    conexao.row_factory = sqlite3.Row
    return conexao


def iniciar_bd():
    conexao = obter_bd()
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            usuario TEXT PRIMARY KEY, senha_hash TEXT NOT NULL,
            verificado INTEGER DEFAULT 0, foto_perfil TEXT, email TEXT
        )
    """)
    for coluna, tipo in [("verificado", "INTEGER DEFAULT 0"), ("foto_perfil", "TEXT"), ("email", "TEXT")]:
        try:
            conexao.execute(f"ALTER TABLE usuarios ADD COLUMN {coluna} {tipo}")
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
    conexao.commit()
    conexao.close()


iniciar_bd()


def buscar_usuario(nome):
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM usuarios WHERE usuario = ? COLLATE NOCASE", (nome,)).fetchone()
    conexao.close()
    return linha


ICONE_MIC = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 14a3 3 0 003-3V6a3 3 0 00-6 0v5a3 3 0 003 3zm5-3a5 5 0 01-10 0H5a7 7 0 006 6.92V21h2v-3.08A7 7 0 0019 11h-2z"/></svg>"""
ICONE_MIC_OFF = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 11h-2a5 5 0 01-8.11 3.91L7.5 16.3A7 7 0 0017 11h2zM4.27 3L3 4.27l6 6V11a3 3 0 003 3c.2 0 .38-.03.56-.06l1.55 1.55A5 5 0 0112 9v.73L19.73 21 21 19.73 4.27 3z"/></svg>"""
ICONE_ONDA = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M3 10v4h3l4 4V6l-4 4H3zm10.5 2a4.5 4.5 0 00-2.5-4.03v8.06A4.5 4.5 0 0013.5 12zM13 3.23v2.06c3.39.87 5.5 4.24 4.63 7.63A6.98 6.98 0 0113 17.71v2.06c4.5-.93 7.44-5.33 6.51-9.83A8.02 8.02 0 0013 3.23z"/></svg>"""
SELO_VERIFICADO = """<svg viewBox="0 0 24 24" width="14" height="14" fill="#fff" style="vertical-align:middle;margin-left:3px;"><path d="M12 2l2.4 2.4 3.3-.5.8 3.3 3.1 1.4-1.1 3.2 1.1 3.2-3.1 1.4-.8 3.3-3.3-.5L12 22l-2.4-2.4-3.3.5-.8-3.3-3.1-1.4 1.1-3.2-1.1-3.2 3.1-1.4.8-3.3 3.3.5z"/><path d="M9.5 12.5l1.8 1.8 3.2-4" stroke="#000" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
AVATAR_PADRAO = "https://api.dicebear.com/7.x/identicon/svg?seed="

ESTILO_COMUM = """
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body { margin:0; font-family: 'Segoe UI', Arial, sans-serif; background:#000000; color:#f2f2f2; }
"""

# ---------- SPLASH ----------
PAGINA_CARREGANDO = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Jarvis</title>
<style>
""" + ESTILO_COMUM + """
body { height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; }
.logo-hud { width:110px; height:110px; position:relative; margin-bottom:24px; }
.anel { position:absolute; border-radius:50%; border:2px solid #ffffff; opacity:0.85; }
.anel1 { inset:0; animation: girar 6s linear infinite; }
.anel2 { inset:14px; border-color:#cccccc; animation: girar 4s linear infinite reverse; }
.anel3 { inset:30px; border-color:#999999; animation: girar 3s linear infinite; }
@keyframes girar { from { transform: rotate(0deg);} to { transform: rotate(360deg);} }
h1 { letter-spacing:4px; font-size:22px; margin:0; }
.credito { position:absolute; bottom:30px; color:#666666; font-size:12px; letter-spacing:1px; opacity:0.6; }
</style></head>
<body>
<div class="logo-hud"><div class="anel anel1"></div><div class="anel anel2"></div><div class="anel anel3"></div></div>
<h1>JARVIS</h1>
<div class="credito">feito por samuca</div>
<script>setTimeout(() => { window.location.href = "/inicio"; }, 1800);</script>
</body></html>
"""

# ---------- LOGIN (tela cheia) ----------
PAGINA_LOGIN = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Jarvis</title>
<style>
""" + ESTILO_COMUM + """
body { height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:24px; }
.logo-img { width:84px; height:84px; border-radius:50%; margin-bottom:18px; box-shadow:0 0 20px #ffffff33; }
h2 { margin:0 0 24px; letter-spacing:1px; font-weight:300; }
form { width:100%; max-width:320px; display:flex; flex-direction:column; gap:12px; }
input { width:100%; padding:16px; border-radius:10px; border:1px solid #ffffff33; background:#0d0d0d; color:#f2f2f2; font-size:15px; }
button { width:100%; padding:16px; border-radius:10px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; font-size:15px; margin-top:6px; }
.trocar { margin-top:20px; font-size:13px; color:#888888; cursor:pointer; text-decoration:underline; }
.erro { color:#ff6666; font-size:13px; margin-top:12px; text-align:center; }
</style></head>
<body>
<img src="/static/logo.jpg" class="logo-img" onerror="this.style.display='none'">
<h2 id="titulo">Entrar no Jarvis</h2>
<form method="POST" id="formulario">
  <input type="text" name="usuario" placeholder="Usuario ou email" required autocomplete="username">
  <input type="email" name="email" id="campoEmail" placeholder="Email" style="display:none;" autocomplete="email">
  <input type="password" name="senha" placeholder="Senha" required autocomplete="current-password">
  <input type="hidden" name="acao" id="acaoCampo" value="login">
  <button type="submit" id="botaoEnviar">Entrar</button>
</form>
<div class="erro">{erro}</div>
<div class="trocar" onclick="alternar()">Nao tem conta? Cadastre-se</div>
<script>
let modoCadastro = false;
function alternar() {
    modoCadastro = !modoCadastro;
    document.getElementById("titulo").textContent = modoCadastro ? "Criar conta" : "Entrar no Jarvis";
    document.getElementById("botaoEnviar").textContent = modoCadastro ? "Cadastrar" : "Entrar";
    document.getElementById("acaoCampo").value = modoCadastro ? "cadastro" : "login";
    document.getElementById("campoEmail").style.display = modoCadastro ? "block" : "none";
    document.getElementById("campoEmail").required = modoCadastro;
    document.querySelector('input[name="usuario"]').placeholder = modoCadastro ? "Usuario (nome de exibicao)" : "Usuario ou email";
    document.querySelector(".trocar").textContent = modoCadastro ? "Ja tem conta? Entrar" : "Nao tem conta? Cadastre-se";
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
.logo-linha { display:flex; align-items:center; gap:10px; margin-bottom:20px; cursor:pointer; }
.menu-icone { display:flex; flex-direction:column; gap:4px; padding:6px; }
.menu-icone span { width:20px; height:2px; background:#ffffff; display:block; }
.logo-img { width:36px; height:36px; border-radius:50%; object-fit:cover; box-shadow:0 0 12px #ffffff55; }
.novo-chat { background:#1a1a1a; color:#f2f2f2; border:1px solid #ffffff33; border-radius:8px; padding:10px; text-align:left; cursor:pointer; margin-bottom:10px; }
.link-rede, .link-inicio { background:#1a1a1a; color:#f2f2f2; border:1px solid #ffffff33; border-radius:8px; padding:10px; text-align:left; cursor:pointer; margin-bottom:8px; display:block; text-decoration:none; }
.historico-lista { flex:1; overflow-y:auto; font-size:13px; margin-top:8px; }
.item-hist { padding:8px; border-radius:6px; margin-bottom:4px; cursor:pointer; color:#cccccc; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.item-hist:hover, .item-hist.ativo { background:#1a1a1a; }
.rodape-sidebar { font-size:12px; color:#aaaaaa; display:flex; justify-content:space-between; align-items:center; margin-top:10px; gap:6px; }
.sair { cursor:pointer; text-decoration:underline; }
.selo-dev { background:#ffffff; color:#000000; font-size:10px; padding:2px 6px; border-radius:8px; font-weight:bold; }
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
.fechar-voz { position:absolute; top:20px; right:20px; width:40px; height:40px; border-radius:50%; background:#1a1a1a; color:#fff; border:1px solid #ffffff33; font-size:20px; cursor:pointer; }
.circulo-voz { width:160px; height:160px; border-radius:50%; border:2px solid #ffffff55; display:flex; align-items:center; justify-content:center; position:relative; }
.circulo-voz .nucleo { width:70px; height:70px; border-radius:50%; background:#111; border:1px solid #fff; transition: transform 0.15s ease; }
.circulo-voz.falando .nucleo { transform: scale(1.25); background:#fff2; }
.circulo-voz.ouvindo .nucleo { transform: scale(1.1); }
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
  .circulo-voz { width:120px; height:120px; }
}
</style></head>
<body>
<div class="sidebar" id="sidebar">
  <div class="logo-linha" onclick="alternarSidebar()">
    <div class="menu-icone"><span></span><span></span><span></span></div>
    <img src="/static/logo.jpg" class="logo-img" onerror="this.style.display='none'">
    <strong>Jarvis</strong>
  </div>
  <a class="link-inicio" href="/inicio">Tela inicial</a>
  <button class="novo-chat" onclick="novaConversa()">+ Nova conversa</button>
  <a class="link-rede" href="/rede">JarvisWEB</a>
  <div class="historico-lista" id="listaConversas"></div>
  <div class="rodape-sidebar">
    <img class="avatar-pequeno" src="{avatar_url}" onclick="location.href='/perfil/{usuario}'">
    <span style="flex:1;">{usuario} {selo_dev}</span>
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
  <div class="fechar-voz" onclick="fecharModoVoz()">&times;</div>
  <div class="circulo-voz" id="circuloVoz">
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

let reconhecimento = null;
let gravando = false;
function alternarMicrofone() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) { alert("Seu navegador nao suporta reconhecimento de voz. Tente no Chrome."); return; }
    const botao = document.getElementById("botaoMic");
    if (gravando) { reconhecimento.stop(); return; }
    reconhecimento = new SpeechRecognition();
    reconhecimento.lang = "pt-BR";
    reconhecimento.interimResults = false;
    reconhecimento.onstart = () => { gravando = true; botao.classList.add("gravando"); botao.innerHTML = ICONE_MIC_OFF_HTML; };
    reconhecimento.onend = () => { gravando = false; botao.classList.remove("gravando"); botao.innerHTML = ICONE_MIC_HTML; };
    reconhecimento.onresult = (evento) => { document.getElementById("campo").value = evento.results[0][0].transcript; enviarTexto(); };
    reconhecimento.start();
}

// ---- Modo de voz continuo (tela cheia) ----
let modoVozAtivo = false;
let reconhecimentoVoz = null;

function abrirModoVoz() {
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
.post img.post-imagem, .post video { max-width:100%; border-radius:8px; margin-top:6px; }
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
.painel-admin input { padding:8px; border-radius:6px; border:1px solid #ffffff22; background:#000000; color:#f2f2f2; margin-right:6px; margin-top:6px; }
.painel-admin button { padding:8px 14px; border-radius:6px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; margin-top:6px; }
@media (max-width:480px) { .container { padding:10px; } }
</style></head>
<body>
<div class="topo"><a href="/inicio" class="voltar">&#8592;</a><span class="titulo-topo">JarvisWEB</span></div>
<div class="container">
  {painel_admin}
  <div class="caixa-postar">
    <textarea id="textoPost" placeholder="No que voce esta pensando?"></textarea>
    <input type="file" id="imagemPost" accept="image/*">
    <input type="text" id="videoPost" placeholder="Link de video (Discord ou outro, opcional)">
    <br><button onclick="publicar()">Postar</button>
  </div>
  <div id="feed"></div>
</div>
<script>
const usuarioLogado = "{usuario}";
async function carregarFeed() {
    const resposta = await fetch("/rede/feed");
    const posts = await resposta.json();
    const div = document.getElementById("feed");
    div.innerHTML = "";
    posts.forEach(p => {
        const bloco = document.createElement("div");
        bloco.className = "post";
        const selo = p.verificado ? ' """ + SELO_VERIFICADO.replace('"', "'") + """' : '';
        let html = '<div class="post-cabecalho"><a href="/perfil/' + p.usuario + '"><img src="' + p.avatar + '">' + p.usuario + selo + '</a></div>';
        if (p.texto) html += '<div class="post-texto"></div>';
        if (p.imagem) html += "<img class='post-imagem' src='" + p.imagem + "'>";
        if (p.video) html += "<video controls src='" + p.video + "'></video>";
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
    const video = document.getElementById("videoPost").value.trim();
    if (!texto && !arquivo && !video) {
        alert("Escreva algo, escolha uma foto ou cole um link de video antes de postar.");
        return;
    }
    const form = new FormData();
    form.append("texto", texto);
    if (arquivo) form.append("imagem", arquivo);
    if (video) form.append("video", video);
    botao.disabled = true;
    botao.textContent = "Postando...";
    try {
        const resposta = await fetch("/rede/postar", { method: "POST", body: form });
        if (!resposta.ok) {
            alert("Nao consegui postar. Sua sessao pode ter expirado - tenta sair e logar de novo.");
            return;
        }
        document.getElementById("textoPost").value = "";
        document.getElementById("imagemPost").value = "";
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
.container { max-width:600px; margin:0 auto; padding:16px; }
.cabecalho-perfil { display:flex; align-items:center; gap:20px; margin-bottom:20px; flex-wrap:wrap; }
.cabecalho-perfil img { width:90px; height:90px; border-radius:50%; object-fit:cover; border:2px solid #ffffff33; }
.stats { display:flex; gap:20px; margin-top:8px; font-size:14px; }
.stats b { display:block; font-size:16px; }
.botao-seguir { padding:8px 18px; border-radius:8px; border:none; background:#ffffff; color:#000000; font-weight:bold; cursor:pointer; margin-top:10px; }
.botao-seguir.ativo { background:#1a1a1a; color:#f2f2f2; border:1px solid #ffffff33; }
.editar-perfil { background:#1a1a1a; border:1px solid #ffffff33; border-radius:10px; padding:14px; margin-bottom:20px; font-size:13px; }
.editar-perfil input { width:100%; padding:8px; margin-top:6px; border-radius:6px; border:1px solid #ffffff22; background:#000; color:#f2f2f2; }
.editar-perfil button { margin-top:8px; padding:8px 14px; border-radius:6px; border:none; background:#ffffff; color:#000; font-weight:bold; cursor:pointer; }
.grade { display:grid; grid-template-columns: repeat(3, 1fr); gap:4px; }
.grade-item { aspect-ratio:1; background:#0d0d0d; border-radius:4px; overflow:hidden; display:flex; align-items:center; justify-content:center; }
.grade-item img, .grade-item video { width:100%; height:100%; object-fit:cover; }
.grade-item.sem-midia { font-size:12px; color:#aaaaaa; padding:8px; text-align:center; }
@media (max-width:480px) { .container { padding:10px; } .cabecalho-perfil img { width:70px; height:70px; } }
</style></head>
<body>
<div class="topo"><a href="/rede" class="voltar">&#8592;</a><span>Perfil</span></div>
<div class="container">
  <div class="cabecalho-perfil">
    <img src="{avatar_url}">
    <div>
      <h2 style="margin:0;">{nome_usuario} {selo}</h2>
      <div class="stats"><div><b>{qtd_posts}</b>posts</div><div><b>{qtd_seguidores}</b>seguidores</div><div><b>{qtd_seguindo}</b>seguindo</div></div>
      {botao_seguir}
    </div>
  </div>
  {editor_perfil}
  <div class="grade">{itens_grade}</div>
</div>
<script>
async function seguirPerfil(alvo) {
    await fetch("/rede/seguir", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo: alvo}) });
    location.reload();
}
async function salvarPerfil() {
    const avatar = document.getElementById("novoAvatar").value.trim();
    const email = document.getElementById("novoEmail").value.trim();
    await fetch("/perfil/editar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({avatar: avatar, email: email}) });
    location.reload();
}
</script>
</body></html>
"""


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "").strip()
        acao = request.form.get("acao", "login")
        if not usuario or not senha:
            return PAGINA_LOGIN.replace("{erro}", "Preencha usuario e senha.")
        conexao = obter_bd()
        if acao == "cadastro":
            if not email:
                conexao.close()
                return PAGINA_LOGIN.replace("{erro}", "Preencha o email tambem.")
            existente = conexao.execute("SELECT * FROM usuarios WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchone()
            if existente:
                conexao.close()
                return PAGINA_LOGIN.replace("{erro}", "Esse usuario ja existe.")
            email_em_uso = conexao.execute("SELECT * FROM usuarios WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
            if email_em_uso:
                conexao.close()
                return PAGINA_LOGIN.replace("{erro}", "Esse email ja esta cadastrado.")
            conexao.execute(
                "INSERT INTO usuarios (usuario, senha_hash, verificado, email) VALUES (?, ?, 0, ?)",
                (usuario, generate_password_hash(senha), email),
            )
            conexao.commit()
            conexao.close()
            session["usuario"] = usuario
            return redirect(url_for("carregando"))
        linha = conexao.execute(
            "SELECT * FROM usuarios WHERE usuario = ? COLLATE NOCASE OR email = ? COLLATE NOCASE", (usuario, usuario)
        ).fetchone()
        conexao.close()
        if linha and check_password_hash(linha["senha_hash"], senha):
            session["usuario"] = linha["usuario"]
            return redirect(url_for("carregando"))
        return PAGINA_LOGIN.replace("{erro}", "Usuario/email ou senha incorretos.")
    if session.get("usuario"):
        return redirect(url_for("inicio"))
    return PAGINA_LOGIN.replace("{erro}", "")


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
    selo = '<span class="selo-dev">DEV</span>' if usuario.upper() == CONTA_DESENVOLVEDOR else ""
    return PAGINA.replace("{usuario}", usuario).replace("{selo_dev}", selo).replace("{avatar_url}", avatar)


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
    eh_dev = usuario.upper() == CONTA_DESENVOLVEDOR
    painel_admin_html = ""
    if eh_dev:
        painel_admin_html = """
        <div class="painel-admin"><b>Painel do desenvolvedor</b><br>Dar selo verificado:
        <input id="alvoVerificar" placeholder="usuario"><input id="pinVerificar" placeholder="PIN" type="password">
        <button onclick="verificar()">Verificar</button>
        <div id="resultadoVerificar" style="margin-top:6px;color:#ffffff;"></div></div>
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
    conexao = obter_bd()
    posts = conexao.execute("SELECT * FROM posts WHERE usuario = ? ORDER BY id DESC", (nome_real,)).fetchall()
    qtd_seguidores = conexao.execute("SELECT COUNT(*) as c FROM seguidores WHERE seguido = ?", (nome_real,)).fetchone()["c"]
    qtd_seguindo = conexao.execute("SELECT COUNT(*) as c FROM seguidores WHERE seguidor = ?", (nome_real,)).fetchone()["c"]
    ja_segue = conexao.execute("SELECT 1 FROM seguidores WHERE seguidor = ? AND seguido = ?", (usuario_logado, nome_real)).fetchone()
    conexao.close()
    itens_grade = ""
    for p in posts:
        if p["imagem"]:
            itens_grade += f'<div class="grade-item"><img src="{p["imagem"]}"></div>'
        elif p["video"]:
            itens_grade += f'<div class="grade-item"><video src="{p["video"]}"></video></div>'
        else:
            itens_grade += f'<div class="grade-item sem-midia">{(p["texto"] or "")[:40]}</div>'
    if nome_real == usuario_logado:
        botao_seguir = ""
        editor_perfil = f"""
        <div class="editar-perfil"><b>Editar perfil</b>
        <input id="novoAvatar" placeholder="Link da foto de perfil (Discord, etc)" value="{linha_alvo['foto_perfil'] or ''}">
        <input id="novoEmail" placeholder="Email (opcional)" value="{linha_alvo['email'] or ''}">
        <button onclick="salvarPerfil()">Salvar</button></div>
        """
    else:
        classe_ativo = "ativo" if ja_segue else ""
        texto_botao = "Seguindo" if ja_segue else "Seguir"
        botao_seguir = f'<button class="botao-seguir {classe_ativo}" onclick="seguirPerfil(\'{nome_real}\')">{texto_botao}</button>'
        editor_perfil = ""
    pagina = PAGINA_PERFIL.replace("{nome_usuario}", nome_real).replace("{avatar_url}", avatar).replace("{selo}", selo)
    pagina = pagina.replace("{qtd_posts}", str(len(posts))).replace("{qtd_seguidores}", str(qtd_seguidores)).replace("{qtd_seguindo}", str(qtd_seguindo))
    pagina = pagina.replace("{botao_seguir}", botao_seguir).replace("{editor_perfil}", editor_perfil).replace("{itens_grade}", itens_grade)
    return pagina


@app.route("/perfil/editar", methods=["POST"])
def perfil_editar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    dados = request.get_json()
    avatar = dados.get("avatar", "").strip()
    email = dados.get("email", "").strip()
    conexao = obter_bd()
    conexao.execute("UPDATE usuarios SET foto_perfil = ?, email = ? WHERE usuario = ?", (avatar or None, email or None, usuario))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/rede/postar", methods=["POST"])
def rede_postar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    texto = request.form.get("texto", "").strip()
    video = request.form.get("video", "").strip()
    arquivo = request.files.get("imagem")
    caminho_imagem = None
    if arquivo and arquivo.filename:
        nome_seguro = secure_filename(arquivo.filename)
        nome_unico = f"{uuid.uuid4().hex}_{nome_seguro}"
        arquivo.save(os.path.join(PASTA_UPLOADS, nome_unico))
        caminho_imagem = f"/static/uploads/{nome_unico}"
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
        linha_autor = conexao.execute("SELECT verificado, foto_perfil FROM usuarios WHERE usuario = ?", (p["usuario"],)).fetchone()
        verificado = bool(linha_autor and linha_autor["verificado"])
        avatar = (linha_autor["foto_perfil"] if linha_autor and linha_autor["foto_perfil"] else AVATAR_PADRAO + p["usuario"])
        resultado.append({
            "id": p["id"], "usuario": p["usuario"], "texto": p["texto"], "imagem": p["imagem"], "video": p["video"],
            "avatar": avatar, "curtidas": curtidas, "curtido": curtido, "seguindo": seguindo, "verificado": verificado,
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
    if not session.get("usuario") or session["usuario"].upper() != CONTA_DESENVOLVEDOR:
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


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)
