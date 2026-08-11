"""
Jarvis - Site Publico
Chat + geracao de imagem, com cadastro de usuario e senha.
NAO tem nenhum comando de controle de PC.
"""

import os
import sqlite3
import urllib.parse
import random
from datetime import datetime
from flask import Flask, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from groq import Groq
except ImportError:
    Groq = None

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


def obter_bd():
    conexao = sqlite3.connect(CAMINHO_BD)
    conexao.row_factory = sqlite3.Row
    return conexao


def iniciar_bd():
    conexao = obter_bd()
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            usuario TEXT PRIMARY KEY,
            senha_hash TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            remetente TEXT NOT NULL,
            texto TEXT NOT NULL,
            criado_em TEXT NOT NULL
        )
    """)
    conexao.commit()
    conexao.close()


iniciar_bd()

ICONE_MIC_LIGADO = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 14a3 3 0 003-3V6a3 3 0 00-6 0v5a3 3 0 003 3zm5-3a5 5 0 01-10 0H5a7 7 0 006 6.92V21h2v-3.08A7 7 0 0019 11h-2z"/></svg>"""
ICONE_MIC_DESLIGADO = """<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 11h-2a5 5 0 01-8.11 3.91L7.5 16.3A7 7 0 0017 11h2zM4.27 3L3 4.27l6 6V11a3 3 0 003 3c.2 0 .38-.03.56-.06l1.55 1.55A5 5 0 0112 9v.73L19.73 21 21 19.73 4.27 3z"/></svg>"""

PAGINA_LOGIN = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis</title>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family:'Segoe UI',Arial,sans-serif; background:#020a12; color:#e6f7ff; display:flex; align-items:center; justify-content:center; height:100vh; }
.caixa { background:#03111c; padding:40px; border-radius:16px; border:1px solid #00c8ff44; text-align:center; width:320px; box-shadow:0 0 30px #00c8ff22; }
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

PAGINA = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis</title>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family: 'Segoe UI', Arial, sans-serif; background:#020a12; color:#e6f7ff; display:flex; height:100vh; }

.sidebar { width:260px; background:#03111c; border-right:1px solid #0a2a3a; padding:16px; display:flex; flex-direction:column; }
.logo-linha { display:flex; align-items:center; gap:10px; margin-bottom:20px; }
.menu-icone { display:flex; flex-direction:column; gap:4px; cursor:pointer; }
.menu-icone span { width:20px; height:2px; background:#00c8ff; display:block; }
.logo-img { width:36px; height:36px; border-radius:50%; object-fit:cover; box-shadow:0 0 12px #00c8ffaa; }
.novo-chat { background:#0a2a3a; color:#8fe8ff; border:1px solid #00c8ff44; border-radius:8px; padding:10px; text-align:left; cursor:pointer; margin-bottom:16px; }
.historico-lista { flex:1; overflow-y:auto; font-size:13px; }
.item-hist { padding:8px; border-radius:6px; margin-bottom:4px; cursor:pointer; color:#8fe8ff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.item-hist:hover { background:#0a2a3a; }
.rodape-sidebar { font-size:12px; color:#4fb8d8; display:flex; justify-content:space-between; align-items:center; margin-top:10px; }
.sair { cursor:pointer; text-decoration:underline; }

.principal { flex:1; display:flex; flex-direction:column; }
.topo { padding:16px; border-bottom:1px solid #0a2a3a; font-weight:bold; letter-spacing:1px; display:flex; align-items:center; justify-content:space-between; }
.seletor-voz { background:#03111c; color:#8fe8ff; border:1px solid #00c8ff44; border-radius:6px; padding:6px; font-size:12px; }
.mensagens { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:16px; }
.msg { max-width:70%; padding:12px 16px; border-radius:12px; line-height:1.4; }
.msg.usuario { align-self:flex-end; background:#0a2a3a; color:#e6f7ff; }
.msg.jarvis { align-self:flex-start; background:#04141f; border:1px solid #00c8ff33; color:#c8faff; }
.msg img { max-width:100%; border-radius:8px; margin-top:8px; }
.botao-falar-msg { background:none; border:none; color:#00c8ff; cursor:pointer; font-size:14px; margin-top:6px; }

.spinner { width:28px; height:28px; border-radius:50%; border:3px solid #0a2a3a; border-top-color:#00c8ff; animation: girar 0.8s linear infinite; }
@keyframes girar { to { transform: rotate(360deg); } }
.pontos-carregando { display:flex; gap:4px; padding:4px 0; }
.pontos-carregando span { width:6px; height:6px; border-radius:50%; background:#00c8ff; animation: pulsar 1s infinite ease-in-out; }
.pontos-carregando span:nth-child(2) { animation-delay: 0.15s; }
.pontos-carregando span:nth-child(3) { animation-delay: 0.3s; }
@keyframes pulsar { 0%, 80%, 100% { opacity:0.2; transform:scale(0.8);} 40% { opacity:1; transform:scale(1.2);} }

.area-input { padding:16px; border-top:1px solid #0a2a3a; display:flex; gap:10px; align-items:center; }
.area-input input { flex:1; padding:14px; border-radius:8px; border:1px solid #00c8ff44; background:#03111c; color:#e6f7ff; font-size:14px; }
.area-input button { padding:14px 18px; border-radius:8px; border:none; background:#00c8ff; color:#001824; font-weight:bold; cursor:pointer; }
.area-input button.imagem { background:#0a2a3a; color:#8fe8ff; border:1px solid #00c8ff44; }
.botao-mic { background:#0a2a3a !important; color:#8fe8ff !important; border:1px solid #00c8ff44 !important; padding:12px 14px !important; display:flex; align-items:center; }
.botao-mic.gravando { background:#ff3b3b !important; color:#fff !important; }
</style>
</head>
<body>

<div class="sidebar">
  <div class="logo-linha">
    <div class="menu-icone"><span></span><span></span><span></span></div>
    <img src="/static/logo.jpg" class="logo-img" onerror="this.style.display='none'">
    <strong>Jarvis</strong>
  </div>
  <button class="novo-chat" onclick="novaConversa()">+ Nova conversa</button>
  <div class="historico-lista" id="listaConversas"></div>
  <div class="rodape-sidebar">
    <span>{usuario}</span>
    <span class="sair" onclick="location.href='/logout'">Sair</span>
  </div>
</div>

<div class="principal">
  <div class="topo">
    <span>JARVIS</span>
    <select class="seletor-voz" id="seletorVoz"></select>
  </div>
  <div class="mensagens" id="mensagens"></div>
  <div class="area-input">
    <button class="botao-mic" id="botaoMic" onclick="alternarMicrofone()">""" + ICONE_MIC_LIGADO + """</button>
    <input type="text" id="campo" placeholder="Pergunte qualquer coisa..." autocomplete="off">
    <button class="imagem" onclick="gerarImagem()">Gerar imagem</button>
    <button onclick="enviarTexto()">Enviar</button>
  </div>
</div>

<script>
const ICONE_LIGADO = `""" + ICONE_MIC_LIGADO + """`;
const ICONE_DESLIGADO = `""" + ICONE_MIC_DESLIGADO + """`;

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

function adicionarMensagem(remetente, conteudoHtml, comAudio) {
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

function adicionarCarregandoTexto() {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg jarvis";
    bolha.innerHTML = '<div class="pontos-carregando"><span></span><span></span><span></span></div>';
    div.appendChild(bolha);
    div.scrollTop = div.scrollHeight;
    return bolha;
}

function adicionarCarregandoImagem() {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg jarvis";
    bolha.innerHTML = '<div class="spinner"></div>';
    div.appendChild(bolha);
    div.scrollTop = div.scrollHeight;
    return bolha;
}

function atualizarListaConversas() {
    const lista = JSON.parse(localStorage.getItem("jarvis_conversas") || "[]");
    const container = document.getElementById("listaConversas");
    container.innerHTML = "";
    lista.forEach(titulo => {
        const item = document.createElement("div");
        item.className = "item-hist";
        item.textContent = titulo.length > 10 ? titulo.slice(0, 10) + "..." : titulo;
        container.appendChild(item);
    });
}

function registrarTituloConversa(primeiroTexto) {
    const lista = JSON.parse(localStorage.getItem("jarvis_conversas") || "[]");
    lista.unshift(primeiroTexto);
    localStorage.setItem("jarvis_conversas", JSON.stringify(lista.slice(0, 20)));
    atualizarListaConversas();
}

function novaConversa() {
    document.getElementById("mensagens").innerHTML = "";
}

let primeiraMensagem = true;

async function enviarTexto() {
    const campo = document.getElementById("campo");
    const texto = campo.value.trim();
    if (!texto) return;
    if (primeiraMensagem) { registrarTituloConversa(texto); primeiraMensagem = false; }
    adicionarMensagem("usuario", texto);
    campo.value = "";

    const carregando = adicionarCarregandoTexto();
    const resposta = await fetch("/chat", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({mensagem: texto})
    });
    const dados = await resposta.json();
    carregando.remove();
    adicionarMensagem("jarvis", dados.resposta, dados.resposta);
}

async function gerarImagem() {
    const campo = document.getElementById("campo");
    const prompt = campo.value.trim();
    if (!prompt) return;
    if (primeiraMensagem) { registrarTituloConversa(prompt); primeiraMensagem = false; }
    adicionarMensagem("usuario", "Gerar imagem: " + prompt);
    campo.value = "";

    const carregando = adicionarCarregandoImagem();
    const resposta = await fetch("/imagem?prompt=" + encodeURIComponent(prompt));
    const dados = await resposta.json();
    carregando.remove();

    const img = new Image();
    img.onload = () => adicionarMensagem("jarvis", "Aqui esta:<br><img src='" + dados.url + "'>");
    img.onerror = () => adicionarMensagem("jarvis", "Nao consegui gerar a imagem, tenta de novo.");
    img.src = dados.url;
}

document.getElementById("campo").addEventListener("keydown", function(e) {
    if (e.key === "Enter") enviarTexto();
});

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
    reconhecimento.onresult = (evento) => {
        document.getElementById("campo").value = evento.results[0][0].transcript;
        enviarTexto();
    };
    reconhecimento.start();
}

atualizarListaConversas();
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
                "INSERT INTO usuarios (usuario, senha_hash) VALUES (?, ?)",
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
    return PAGINA.replace("{usuario}", session["usuario"])


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


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)
