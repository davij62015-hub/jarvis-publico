"""
Jarvis - Site Publico
Chat + geracao/busca de imagem + conversor de imagem + rede social basica (JarvisWEB).
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
    "Responda em portugues do Brasil, de forma clara e amigavel, curto e direto. "
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
            usuario TEXT PRIMARY KEY,
            senha_hash TEXT NOT NULL,
            verificado INTEGER DEFAULT 0
        )
    """)
    try:
        conexao.execute("ALTER TABLE usuarios ADD COLUMN verificado INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            remetente TEXT NOT NULL,
            texto TEXT NOT NULL,
            criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            texto TEXT,
            imagem TEXT,
            criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS curtidas (
            post_id INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            PRIMARY KEY (post_id, usuario)
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS comentarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            texto TEXT NOT NULL,
            criado_em TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS seguidores (
            seguidor TEXT NOT NULL,
            seguido TEXT NOT NULL,
            PRIMARY KEY (seguidor, seguido)
        )
    """)
    conexao.commit()
    conexao.close()


iniciar_bd()

ICONE_MIC_LIGADO = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 14a3 3 0 003-3V6a3 3 0 00-6 0v5a3 3 0 003 3zm5-3a5 5 0 01-10 0H5a7 7 0 006 6.92V21h2v-3.08A7 7 0 0019 11h-2z"/></svg>"""
ICONE_MIC_DESLIGADO = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 11h-2a5 5 0 01-8.11 3.91L7.5 16.3A7 7 0 0017 11h2zM4.27 3L3 4.27l6 6V11a3 3 0 003 3c.2 0 .38-.03.56-.06l1.55 1.55A5 5 0 0112 9v.73L19.73 21 21 19.73 4.27 3z"/></svg>"""
SELO_VERIFICADO = """<svg viewBox="0 0 24 24" width="14" height="14" fill="#00c8ff" style="vertical-align:middle;margin-left:3px;"><path d="M12 2l2.4 2.4 3.3-.5.8 3.3 3.1 1.4-1.1 3.2 1.1 3.2-3.1 1.4-.8 3.3-3.3-.5L12 22l-2.4-2.4-3.3.5-.8-3.3-3.1-1.4 1.1-3.2-1.1-3.2 3.1-1.4.8-3.3 3.3.5z"/><path d="M9.5 12.5l1.8 1.8 3.2-4" stroke="#020a12" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>"""


def usuario_atual():
    return session.get("usuario")


def eh_verificado(nome_usuario):
    conexao = obter_bd()
    linha = conexao.execute("SELECT verificado FROM usuarios WHERE usuario = ?", (nome_usuario,)).fetchone()
    conexao.close()
    return bool(linha and linha["verificado"])


PAGINA_LOGIN = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis</title>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family:'Segoe UI',Arial,sans-serif; background:#020a12; color:#e6f7ff; display:flex; align-items:center; justify-content:center; height:100vh; }
.caixa { background:#03111c; padding:40px; border-radius:16px; border:1px solid #00c8ff44; text-align:center; width:320px; max-width:90vw; box-shadow:0 0 30px #00c8ff22; }
.logo-img { width:70px; height:70px; border-radius:50%; margin-bottom:10px; box-shadow:0 0 16px #00c8ffaa; }
h2 { margin:10px 0 20px; }
input { width:100%; padding:12px; margin-top:10px; border-radius:8px; border:1px solid #00c8ff44; background:#020a12; color:#e6f7ff; }
button { width:100%; padding:12px; margin-top:16px; border-radius:8px; border:none; background:#00c8ff; color:#001824; font-weight:bold; cursor:pointer; }
.trocar { margin-top:16px; font-size:13px; color:#4fb8d8; cursor:pointer; text-decoration:underline; }
.erro { color:#ff6666; font-size:13px; margin-top:10px; }
</style>
</head>
<body>
<div class="caixa">
  <img src="/static/logo.jpg" class="logo-img" onerror="this.style.display='none'">
  <h2 id="titulo">Entrar no Jarvis</h2>
  <form method="POST" id="formulario">
    <input type="text" name="usuario" placeholder="Usuario" required>
    <input type="password" name="senha" placeholder="Senha" required>
    <input type="hidden" name="acao" id="acaoCampo" value="login">
    <button type="submit" id="botaoEnviar">Entrar</button>
  </form>
  <div class="erro">{erro}</div>
  <div class="trocar" onclick="alternar()">Nao tem conta? Cadastre-se</div>
</div>
<script>
let modoCadastro = false;
function alternar() {
    modoCadastro = !modoCadastro;
    document.getElementById("titulo").textContent = modoCadastro ? "Criar conta" : "Entrar no Jarvis";
    document.getElementById("botaoEnviar").textContent = modoCadastro ? "Cadastrar" : "Entrar";
    document.getElementById("acaoCampo").value = modoCadastro ? "cadastro" : "login";
    document.querySelector(".trocar").textContent = modoCadastro ? "Ja tem conta? Entrar" : "Nao tem conta? Cadastre-se";
}
</script>
</body>
</html>
"""

ESTILO_COMUM = """
* { box-sizing: border-box; }
body { margin:0; font-family: 'Segoe UI', Arial, sans-serif; background:#020a12; color:#e6f7ff; }
"""

PAGINA = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis</title>
<style>
""" + ESTILO_COMUM + """
body { display:flex; height:100vh; overflow:hidden; }

.sidebar { width:260px; background:#03111c; border-right:1px solid #0a2a3a; padding:16px; display:flex; flex-direction:column; transition: margin-left 0.2s ease; flex-shrink:0; }
.sidebar.recolhida { margin-left:-260px; }
.logo-linha { display:flex; align-items:center; gap:10px; margin-bottom:20px; }
.menu-icone { display:flex; flex-direction:column; gap:4px; cursor:pointer; padding:6px; }
.menu-icone span { width:20px; height:2px; background:#00c8ff; display:block; }
.logo-img { width:36px; height:36px; border-radius:50%; object-fit:cover; box-shadow:0 0 12px #00c8ffaa; }
.novo-chat { background:#0a2a3a; color:#8fe8ff; border:1px solid #00c8ff44; border-radius:8px; padding:10px; text-align:left; cursor:pointer; margin-bottom:10px; }
.link-rede { background:#0a2a3a; color:#8fe8ff; border:1px solid #00c8ff44; border-radius:8px; padding:10px; text-align:left; cursor:pointer; margin-bottom:16px; display:block; text-decoration:none; }
.historico-lista { flex:1; overflow-y:auto; font-size:13px; }
.item-hist { padding:8px; border-radius:6px; margin-bottom:4px; cursor:pointer; color:#8fe8ff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.item-hist:hover, .item-hist.ativo { background:#0a2a3a; }
.rodape-sidebar { font-size:12px; color:#4fb8d8; display:flex; justify-content:space-between; align-items:center; margin-top:10px; gap:6px; }
.sair { cursor:pointer; text-decoration:underline; }
.selo-dev { background:#00c8ff; color:#001824; font-size:10px; padding:2px 6px; border-radius:8px; font-weight:bold; }

.principal { flex:1; display:flex; flex-direction:column; min-width:0; }
.topo { padding:16px; border-bottom:1px solid #0a2a3a; font-weight:bold; letter-spacing:1px; display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
.topo-esquerda { display:flex; align-items:center; gap:10px; }
.menu-icone-mobile { display:none; }
.seletor-voz { background:#03111c; color:#8fe8ff; border:1px solid #00c8ff44; border-radius:6px; padding:6px; font-size:12px; }
.mensagens { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:16px; }
.msg { max-width:70%; padding:12px 16px; border-radius:12px; line-height:1.4; }
.msg.usuario { align-self:flex-end; background:#0a2a3a; color:#e6f7ff; }
.msg.jarvis { align-self:flex-start; background:#04141f; border:1px solid #00c8ff33; color:#c8faff; }
.msg img { max-width:100%; border-radius:8px; margin-top:8px; }
.botao-falar-msg { background:none; border:none; color:#00c8ff; cursor:pointer; font-size:14px; margin-top:6px; }

.spinner { width:24px; height:24px; border-radius:50%; border:3px solid #0a2a3a; border-top-color:#00c8ff; animation: girar 0.8s linear infinite; flex-shrink:0; }
@keyframes girar { to { transform: rotate(360deg); } }
.pontos-carregando { display:flex; gap:4px; padding:4px 0; }
.pontos-carregando span { width:6px; height:6px; border-radius:50%; background:#00c8ff; animation: pulsar 1s infinite ease-in-out; }
.pontos-carregando span:nth-child(2) { animation-delay: 0.15s; }
.pontos-carregando span:nth-child(3) { animation-delay: 0.3s; }
@keyframes pulsar { 0%, 80%, 100% { opacity:0.2; transform:scale(0.8);} 40% { opacity:1; transform:scale(1.2);} }

.area-input { padding:16px; border-top:1px solid #0a2a3a; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.area-input input[type=text] { flex:1; min-width:120px; padding:14px; border-radius:8px; border:1px solid #00c8ff44; background:#03111c; color:#e6f7ff; font-size:14px; }
.area-input button { padding:14px 16px; border-radius:8px; border:none; background:#00c8ff; color:#001824; font-weight:bold; cursor:pointer; font-size:13px; }
.area-input button.secundario { background:#0a2a3a; color:#8fe8ff; border:1px solid #00c8ff44; }
.botao-mic { background:#0a2a3a !important; color:#8fe8ff !important; border:1px solid #00c8ff44 !important; padding:12px 14px !important; display:flex; align-items:center; }
.botao-mic.gravando { background:#ff3b3b !important; color:#fff !important; }

.modal-fundo { display:none; position:fixed; inset:0; background:#000a; align-items:center; justify-content:center; z-index:10; }
.modal-fundo.aberto { display:flex; }
.modal-caixa { background:#03111c; border:1px solid #00c8ff44; border-radius:12px; padding:24px; width:320px; max-width:90vw; }
.modal-caixa h3 { margin-top:0; }
.modal-caixa input, .modal-caixa select { width:100%; padding:10px; margin-top:8px; border-radius:6px; border:1px solid #00c8ff44; background:#020a12; color:#e6f7ff; }
.modal-botoes { display:flex; gap:8px; margin-top:16px; }
.modal-botoes button { flex:1; padding:10px; border-radius:6px; border:none; cursor:pointer; font-weight:bold; }
.modal-botoes .confirmar { background:#00c8ff; color:#001824; }
.modal-botoes .cancelar { background:#0a2a3a; color:#8fe8ff; }

@media (max-width: 720px) {
  .sidebar { position:fixed; z-index:20; height:100vh; }
  .sidebar:not(.recolhida) { margin-left:0; }
  .sidebar.recolhida { margin-left:-260px; }
  .msg { max-width:88%; }
  .area-input button { padding:12px; font-size:12px; }
}
</style>
</head>
<body>

<div class="sidebar" id="sidebar">
  <div class="logo-linha">
    <div class="menu-icone" onclick="alternarSidebar()"><span></span><span></span><span></span></div>
    <img src="/static/logo.jpg" class="logo-img" onerror="this.style.display='none'">
    <strong>Jarvis</strong>
  </div>
  <button class="novo-chat" onclick="novaConversa()">+ Nova conversa</button>
  <a class="link-rede" href="/rede">JarvisWEB</a>
  <div class="historico-lista" id="listaConversas"></div>
  <div class="rodape-sidebar">
    <span>{usuario} {selo_dev}</span>
    <span class="sair" onclick="location.href='/logout'">Sair</span>
  </div>
</div>

<div class="principal">
  <div class="topo">
    <div class="topo-esquerda">
      <div class="menu-icone menu-icone-mobile" onclick="alternarSidebar()"><span></span><span></span><span></span></div>
      <span>JARVIS</span>
    </div>
    <select class="seletor-voz" id="seletorVoz"></select>
  </div>
  <div class="mensagens" id="mensagens"></div>
  <div class="area-input">
    <button class="botao-mic" id="botaoMic" onclick="alternarMicrofone()">""" + ICONE_MIC_LIGADO + """</button>
    <input type="text" id="campo" placeholder="Pergunte qualquer coisa...">
    <button class="secundario" onclick="gerarImagem()">Gerar imagem</button>
    <button class="secundario" onclick="buscarImagem()">Buscar imagem</button>
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
    <select id="formatoNovo">
      <option value="png">PNG</option>
      <option value="gif">GIF</option>
      <option value="jpeg">JPEG</option>
    </select>
    <div class="modal-botoes">
      <button class="cancelar" onclick="fecharModalConverter()">Cancelar</button>
      <button class="confirmar" onclick="converterImagem()">Converter</button>
    </div>
  </div>
</div>

<script>
const ICONE_LIGADO = `""" + ICONE_MIC_LIGADO + """`;
const ICONE_DESLIGADO = `""" + ICONE_MIC_DESLIGADO + """`;

function alternarSidebar() { document.getElementById("sidebar").classList.toggle("recolhida"); }

let vozes = [];
function carregarVozes() {
    vozes = speechSynthesis.getVoices().filter(v => v.lang.startsWith("pt"));
    const seletor = document.getElementById("seletorVoz");
    seletor.innerHTML = "";
    if (vozes.length === 0) { seletor.innerHTML = "<option>Nenhuma voz PT</option>"; return; }
    vozes.forEach((v, i) => {
        const opcao = document.createElement("option");
        opcao.value = i;
        opcao.textContent = v.name;
        seletor.appendChild(opcao);
    });
}
speechSynthesis.onvoiceschanged = carregarVozes;
carregarVozes();

function falarTexto(texto) {
    const seletor = document.getElementById("seletorVoz");
    const utter = new SpeechSynthesisUtterance(texto);
    if (vozes[seletor.value]) utter.voice = vozes[seletor.value];
    utter.lang = "pt-BR";
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
        item.onclick = () => abrirConversa(i);
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
    renderizarSidebar();
    renderizarMensagens();
    if (window.innerWidth <= 720) document.getElementById("sidebar").classList.add("recolhida");
}

function novaConversa() {
    conversas.unshift({titulo: "", mensagens: []});
    indiceAtual = 0;
    salvarConversas();
    renderizarSidebar();
    renderizarMensagens();
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
    if (!conversas[indiceAtual].titulo) {
        conversas[indiceAtual].titulo = bolha.textContent.trim();
        renderizarSidebar();
    }
    salvarConversas();
    return bolha;
}

function adicionarCarregandoTexto() {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg jarvis";
    bolha.innerHTML = '<div class="pontos-carregando"><span></span><span></span><span></span></div>';
    div.appendChild(bolha);
    div.scrollTop = div.scrollHeight;
    return bolha;
}

function adicionarCarregandoImagem(texto) {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg jarvis";
    bolha.innerHTML = '<div style="display:flex;align-items:center;gap:10px;"><div class="spinner"></div><span>' + texto + '</span></div>';
    div.appendChild(bolha);
    div.scrollTop = div.scrollHeight;
    return bolha;
}

async function enviarTexto() {
    const campo = document.getElementById("campo");
    const texto = campo.value.trim();
    if (!texto) return;
    adicionarMensagem("usuario", texto);
    campo.value = "";
    const carregando = adicionarCarregandoTexto();
    const resposta = await fetch("/chat", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mensagem: texto}) });
    const dados = await resposta.json();
    carregando.remove();
    adicionarMensagem("jarvis", dados.resposta, dados.resposta);
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
    img.onload = () => adicionarMensagem("jarvis", "Aqui esta (gerada por IA):<br><img src='" + dados.url + "'>");
    img.onerror = () => adicionarMensagem("jarvis", "Nao consegui gerar a imagem, tenta de novo.");
    img.src = dados.url;
}

async function buscarImagem() {
    const campo = document.getElementById("campo");
    const termo = campo.value.trim();
    if (!termo) return;
    adicionarMensagem("usuario", "Buscar imagem: " + termo);
    campo.value = "";
    const carregando = adicionarCarregandoImagem("Buscando imagem...");
    const resposta = await fetch("/buscar_imagem?termo=" + encodeURIComponent(termo));
    const dados = await resposta.json();
    carregando.remove();
    if (!dados.url) { adicionarMensagem("jarvis", "Nao encontrei nenhuma imagem real para isso."); return; }
    adicionarMensagem("jarvis", "Encontrei essa (imagem real, licenca aberta):<br><img src='" + dados.url + "'><br><span style='font-size:11px;opacity:0.7;'>Fonte: " + (dados.fonte || "desconhecida") + "</span>");
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
    dadosForm.append("arquivo", arquivo);
    dadosForm.append("largura", largura);
    dadosForm.append("altura", altura);
    dadosForm.append("formato", formato);
    fecharModalConverter();
    adicionarMensagem("usuario", "Converter imagem: " + arquivo.name);
    const carregando = adicionarCarregandoImagem("Convertendo...");
    const resposta = await fetch("/converter", { method: "POST", body: dadosForm });
    carregando.remove();
    if (!resposta.ok) { adicionarMensagem("jarvis", "Nao consegui converter essa imagem."); return; }
    const blob = await resposta.blob();
    const url = URL.createObjectURL(blob);
    adicionarMensagem("jarvis", "Pronto:<br><img src='" + url + "'><br><a href='" + url + "' download='convertida." + formato + "' style='color:#00c8ff;'>Baixar</a>");
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
    reconhecimento.onstart = () => { gravando = true; botao.classList.add("gravando"); botao.innerHTML = ICONE_DESLIGADO; };
    reconhecimento.onend = () => { gravando = false; botao.classList.remove("gravando"); botao.innerHTML = ICONE_LIGADO; };
    reconhecimento.onresult = (evento) => { document.getElementById("campo").value = evento.results[0][0].transcript; enviarTexto(); };
    reconhecimento.start();
}

if (conversas.length > 0) { indiceAtual = 0; }
renderizarSidebar();
renderizarMensagens();
</script>
</body>
</html>
"""

PAGINA_REDE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JarvisWEB</title>
<style>
""" + ESTILO_COMUM + """
body { height:100vh; overflow-y:auto; }
.topo { position:sticky; top:0; background:#020a12; padding:14px 16px; border-bottom:1px solid #0a2a3a; display:flex; align-items:center; gap:12px; z-index:5; }
.voltar { color:#00c8ff; text-decoration:none; font-size:20px; }
.titulo-topo { font-weight:bold; letter-spacing:1px; }
.container { max-width:600px; margin:0 auto; padding:16px; }
.caixa-postar { background:#03111c; border:1px solid #0a2a3a; border-radius:12px; padding:14px; margin-bottom:20px; }
.caixa-postar textarea { width:100%; background:#020a12; border:1px solid #0a2a3a; border-radius:8px; color:#e6f7ff; padding:10px; resize:vertical; min-height:60px; }
.caixa-postar input[type=file] { margin-top:8px; font-size:12px; }
.caixa-postar button { margin-top:10px; padding:10px 18px; border-radius:8px; border:none; background:#00c8ff; color:#001824; font-weight:bold; cursor:pointer; }
.post { background:#03111c; border:1px solid #0a2a3a; border-radius:12px; padding:14px; margin-bottom:16px; }
.post-cabecalho { display:flex; align-items:center; gap:8px; margin-bottom:8px; font-weight:bold; }
.post-texto { margin:8px 0; white-space:pre-wrap; }
.post img { max-width:100%; border-radius:8px; margin-top:6px; }
.post-acoes { display:flex; gap:16px; margin-top:10px; font-size:13px; }
.post-acoes span { cursor:pointer; color:#8fe8ff; }
.post-acoes span.ativo { color:#00c8ff; font-weight:bold; }
.comentarios { margin-top:10px; border-top:1px solid #0a2a3a; padding-top:8px; font-size:13px; }
.comentario { margin-bottom:6px; }
.comentario b { color:#8fe8ff; }
.caixa-comentar { display:flex; gap:6px; margin-top:6px; }
.caixa-comentar input { flex:1; padding:8px; border-radius:6px; border:1px solid #0a2a3a; background:#020a12; color:#e6f7ff; font-size:12px; }
.caixa-comentar button { padding:8px 12px; border-radius:6px; border:none; background:#0a2a3a; color:#8fe8ff; cursor:pointer; font-size:12px; }
.painel-admin { background:#04141f; border:1px solid #00c8ff44; border-radius:12px; padding:14px; margin-bottom:20px; font-size:13px; }
.painel-admin input { padding:8px; border-radius:6px; border:1px solid #0a2a3a; background:#020a12; color:#e6f7ff; margin-right:6px; margin-top:6px; }
.painel-admin button { padding:8px 14px; border-radius:6px; border:none; background:#00c8ff; color:#001824; font-weight:bold; cursor:pointer; margin-top:6px; }
</style>
</head>
<body>
<div class="topo">
  <a href="/painel" class="voltar">&#8592;</a>
  <span class="titulo-topo">JarvisWEB</span>
</div>
<div class="container">

  {painel_admin}

  <div class="caixa-postar">
    <textarea id="textoPost" placeholder="No que voce esta pensando?"></textarea>
    <input type="file" id="imagemPost" accept="image/*">
    <br><button onclick="publicar()">Postar</button>
  </div>

  <div id="feed"></div>
</div>

<script>
const usuarioLogado = "{usuario}";
const ehDev = {eh_dev};

async function carregarFeed() {
    const resposta = await fetch("/rede/feed");
    const posts = await resposta.json();
    const div = document.getElementById("feed");
    div.innerHTML = "";
    posts.forEach(p => {
        const bloco = document.createElement("div");
        bloco.className = "post";
        const selo = p.verificado ? ' """ + SELO_VERIFICADO.replace('"', "'") + """' : '';
        let html = '<div class="post-cabecalho">' + p.usuario + selo + '</div>';
        if (p.texto) html += '<div class="post-texto"></div>';
        if (p.imagem) html += "<img src='" + p.imagem + "'>";
        html += '<div class="post-acoes">';
        html += '<span class="' + (p.curtido ? 'ativo' : '') + '" onclick="curtir(' + p.id + ')">Curtir (' + p.curtidas + ')</span>';
        html += '<span onclick="mostrarComentarios(' + p.id + ')">Comentar (' + p.comentarios.length + ')</span>';
        if (p.usuario !== usuarioLogado) {
            html += '<span class="' + (p.seguindo ? 'ativo' : '') + '" onclick="seguir(\\'' + p.usuario + '\\')">' + (p.seguindo ? 'Seguindo' : 'Seguir') + '</span>';
        }
        html += '</div>';
        html += '<div class="comentarios" id="coment-' + p.id + '" style="display:none;">';
        p.comentarios.forEach(c => { html += '<div class="comentario"><b>' + c.usuario + ':</b> </div>'; });
        html += '<div class="caixa-comentar"><input id="novoComent-' + p.id + '" placeholder="Comentar..."><button onclick="comentar(' + p.id + ')">Enviar</button></div>';
        html += '</div>';
        bloco.innerHTML = html;
        if (p.texto) bloco.querySelector(".post-texto").textContent = p.texto;
        bloco.querySelectorAll(".comentario").forEach((elemento, i) => {
            elemento.querySelector("b").nextSibling.textContent = " " + p.comentarios[i].texto;
        });
        div.appendChild(bloco);
    });
}

function mostrarComentarios(id) {
    const el = document.getElementById("coment-" + id);
    el.style.display = el.style.display === "none" ? "block" : "none";
}

async function publicar() {
    const texto = document.getElementById("textoPost").value.trim();
    const arquivo = document.getElementById("imagemPost").files[0];
    if (!texto && !arquivo) return;
    const form = new FormData();
    form.append("texto", texto);
    if (arquivo) form.append("imagem", arquivo);
    await fetch("/rede/postar", { method: "POST", body: form });
    document.getElementById("textoPost").value = "";
    document.getElementById("imagemPost").value = "";
    carregarFeed();
}

async function curtir(id) {
    await fetch("/rede/curtir", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({post_id: id}) });
    carregarFeed();
}

async function comentar(id) {
    const campo = document.getElementById("novoComent-" + id);
    const texto = campo.value.trim();
    if (!texto) return;
    await fetch("/rede/comentar", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({post_id: id, texto: texto}) });
    campo.value = "";
    carregarFeed();
}

async function seguir(alvo) {
    await fetch("/rede/seguir", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({alvo: alvo}) });
    carregarFeed();
}

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
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "").strip()
        acao = request.form.get("acao", "login")

        if not usuario or not senha:
            return PAGINA_LOGIN.replace("{erro}", "Preencha usuario e senha.")

        conexao = obter_bd()

        if acao == "cadastro":
            existente = conexao.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
            if existente:
                conexao.close()
                return PAGINA_LOGIN.replace("{erro}", "Esse usuario ja existe.")
            conexao.execute(
                "INSERT INTO usuarios (usuario, senha_hash, verificado) VALUES (?, ?, 0)",
                (usuario, generate_password_hash(senha)),
            )
            conexao.commit()
            conexao.close()
            session["usuario"] = usuario
            return redirect(url_for("painel"))

        linha = conexao.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        conexao.close()
        if linha and check_password_hash(linha["senha_hash"], senha):
            session["usuario"] = usuario
            return redirect(url_for("painel"))
        return PAGINA_LOGIN.replace("{erro}", "Usuario ou senha incorretos.")

    if session.get("usuario"):
        return redirect(url_for("painel"))
    return PAGINA_LOGIN.replace("{erro}", "")


@app.route("/painel")
def painel():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    usuario = session["usuario"]
    selo = '<span class="selo-dev">DEV</span>' if usuario.upper() == CONTA_DESENVOLVEDOR else ""
    return PAGINA.replace("{usuario}", usuario).replace("{selo_dev}", selo)


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
    conexao.execute(
        "INSERT INTO mensagens (usuario, remetente, texto, criado_em) VALUES (?, ?, ?, ?)",
        (usuario, "usuario", mensagem, datetime.now().isoformat()),
    )
    linhas = conexao.execute(
        "SELECT remetente, texto FROM mensagens WHERE usuario = ? ORDER BY id DESC LIMIT 12",
        (usuario,),
    ).fetchall()
    historico_mensagens = [
        {"role": "user" if l["remetente"] == "usuario" else "assistant", "content": l["texto"]}
        for l in reversed(linhas)
    ]

    resposta = _cliente.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": SISTEMA}] + historico_mensagens,
        max_tokens=500,
    )
    texto_resposta = resposta.choices[0].message.content

    conexao.execute(
        "INSERT INTO mensagens (usuario, remetente, texto, criado_em) VALUES (?, ?, ?, ?)",
        (usuario, "jarvis", texto_resposta, datetime.now().isoformat()),
    )
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
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(prompt_melhorado)
        + f"?model=flux&width=1024&height=1024&seed={seed}&nologo=true"
    )
    return jsonify({"url": url})


@app.route("/buscar_imagem")
def buscar_imagem():
    if not session.get("usuario"):
        return jsonify({"url": ""}), 401
    termo = request.args.get("termo", "").strip()
    if not termo or not requests:
        return jsonify({"url": ""})

    try:
        resposta = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": termo, "page_size": 1},
            timeout=8,
        )
        dados = resposta.json()
        resultados = dados.get("results", [])
        if not resultados:
            return jsonify({"url": ""})
        primeiro = resultados[0]
        return jsonify({"url": primeiro.get("url", ""), "fonte": primeiro.get("source", "")})
    except Exception:
        return jsonify({"url": ""})


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
        if formato in ("jpeg", "jpg"):
            img = img.convert("RGB")
        else:
            img = img.convert("RGBA")

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
        <div class="painel-admin">
          <b>Painel do desenvolvedor</b><br>
          Dar selo verificado:
          <input id="alvoVerificar" placeholder="usuario">
          <input id="pinVerificar" placeholder="PIN" type="password">
          <button onclick="verificar()">Verificar</button>
          <div id="resultadoVerificar" style="margin-top:6px;color:#00c8ff;"></div>
        </div>
        """

    pagina = PAGINA_REDE.replace("{usuario}", usuario)
    pagina = pagina.replace("{eh_dev}", "true" if eh_dev else "false")
    pagina = pagina.replace("{painel_admin}", painel_admin_html)
    return pagina


@app.route("/rede/postar", methods=["POST"])
def rede_postar():
    if not session.get("usuario"):
        return jsonify({"ok": False}), 401
    usuario = session["usuario"]
    texto = request.form.get("texto", "").strip()
    arquivo = request.files.get("imagem")

    caminho_imagem = None
    if arquivo and arquivo.filename:
        nome_seguro = secure_filename(arquivo.filename)
        nome_unico = f"{uuid.uuid4().hex}_{nome_seguro}"
        arquivo.save(os.path.join(PASTA_UPLOADS, nome_unico))
        caminho_imagem = f"/static/uploads/{nome_unico}"

    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO posts (usuario, texto, imagem, criado_em) VALUES (?, ?, ?, ?)",
        (usuario, texto, caminho_imagem, datetime.now().isoformat()),
    )
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
        verificado_linha = conexao.execute("SELECT verificado FROM usuarios WHERE usuario = ?", (p["usuario"],)).fetchone()
        verificado = bool(verificado_linha and verificado_linha["verificado"])

        resultado.append({
            "id": p["id"],
            "usuario": p["usuario"],
            "texto": p["texto"],
            "imagem": p["imagem"],
            "curtidas": curtidas,
            "curtido": curtido,
            "seguindo": seguindo,
            "verificado": verificado,
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
    conexao.execute(
        "INSERT INTO comentarios (post_id, usuario, texto, criado_em) VALUES (?, ?, ?, ?)",
        (post_id, usuario, texto, datetime.now().isoformat()),
    )
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
    existe = conexao.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (alvo,)).fetchone()
    if not existe:
        conexao.close()
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})

    conexao.execute("UPDATE usuarios SET verificado = 1 WHERE usuario = ?", (alvo,))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)
