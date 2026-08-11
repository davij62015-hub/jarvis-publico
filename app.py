"""
Jarvis - Site Publico
Versao segura para hospedar na internet: so conversa com IA e gera imagens.
NAO tem nenhum comando de controle de PC (nada de abrir programas, desligar, etc).
"""

import os
import urllib.parse
import random
from flask import Flask, request, jsonify, session

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
.historico-lista { flex:1; overflow-y:auto; font-size:13px; color:#4fb8d8; }

.principal { flex:1; display:flex; flex-direction:column; }
.topo { padding:16px; border-bottom:1px solid #0a2a3a; font-weight:bold; letter-spacing:1px; display:flex; align-items:center; justify-content:space-between; }
.seletor-voz { background:#03111c; color:#8fe8ff; border:1px solid #00c8ff44; border-radius:6px; padding:6px; font-size:12px; }
.mensagens { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:16px; }
.msg { max-width:70%; padding:12px 16px; border-radius:12px; line-height:1.4; }
.msg.usuario { align-self:flex-end; background:#0a2a3a; color:#e6f7ff; }
.msg.jarvis { align-self:flex-start; background:#04141f; border:1px solid #00c8ff33; color:#c8faff; }
.msg img { max-width:100%; border-radius:8px; margin-top:8px; }
.botao-falar-msg { background:none; border:none; color:#00c8ff; cursor:pointer; font-size:14px; margin-top:6px; }

.pontos-carregando { display:flex; gap:4px; padding:4px 0; }
.pontos-carregando span { width:6px; height:6px; border-radius:50%; background:#00c8ff; animation: pulsar 1s infinite ease-in-out; }
.pontos-carregando span:nth-child(2) { animation-delay: 0.15s; }
.pontos-carregando span:nth-child(3) { animation-delay: 0.3s; }
@keyframes pulsar { 0%, 80%, 100% { opacity:0.2; transform:scale(0.8);} 40% { opacity:1; transform:scale(1.2);} }

.area-input { padding:16px; border-top:1px solid #0a2a3a; display:flex; gap:10px; align-items:center; }
.area-input input { flex:1; padding:14px; border-radius:8px; border:1px solid #00c8ff44; background:#03111c; color:#e6f7ff; font-size:14px; }
.area-input button { padding:14px 18px; border-radius:8px; border:none; background:#00c8ff; color:#001824; font-weight:bold; cursor:pointer; }
.area-input button.imagem { background:#0a2a3a; color:#8fe8ff; border:1px solid #00c8ff44; }
.botao-mic { background:#0a2a3a !important; color:#8fe8ff !important; border:1px solid #00c8ff44 !important; font-size:18px !important; padding:14px 16px !important; }
.botao-mic.gravando { background:#ff3b3b !important; color:#fff !important; }
</style>
</head>
<body>

<div class="sidebar">
  <div class="logo-linha">
    <div class="menu-icone"><span></span><span></span><span></span></div>
    <img src="/static/logo.jpg" class="logo-img">
    <strong>Jarvis</strong>
  </div>
  <button class="novo-chat" onclick="location.reload()">+ Nova conversa</button>
  <div class="historico-lista">Desenvolvido por Samuca</div>
</div>

<div class="principal">
  <div class="topo">
    <span>JARVIS</span>
    <select class="seletor-voz" id="seletorVoz"></select>
  </div>
  <div class="mensagens" id="mensagens"></div>
  <div class="area-input">
    <button class="botao-mic" id="botaoMic" onclick="alternarMicrofone()">Mic</button>
    <input type="text" id="campo" placeholder="Pergunte qualquer coisa..." autocomplete="off">
    <button class="imagem" onclick="gerarImagem()">Gerar imagem</button>
    <button onclick="enviarTexto()">Enviar</button>
  </div>
</div>

<script>
let vozes = [];
function carregarVozes() {
    vozes = speechSynthesis.getVoices().filter(v => v.lang.startsWith("pt"));
    const seletor = document.getElementById("seletorVoz");
    seletor.innerHTML = "";
    if (vozes.length === 0) {
        seletor.innerHTML = "<option>Nenhuma voz PT encontrada</option>";
        return;
    }
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
    if (vozes[seletor.value]) {
        utter.voice = vozes[seletor.value];
    }
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

function adicionarCarregando() {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg jarvis";
    bolha.innerHTML = '<div class="pontos-carregando"><span></span><span></span><span></span></div>';
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

    const carregando = adicionarCarregando();

    const resposta = await fetch("/chat", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
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
    adicionarMensagem("usuario", "Gerar imagem: " + prompt);
    campo.value = "";

    const carregando = adicionarCarregando();

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
    if (!SpeechRecognition) {
        alert("Seu navegador nao suporta reconhecimento de voz. Tente no Chrome.");
        return;
    }
    const botao = document.getElementById("botaoMic");

    if (gravando) {
        reconhecimento.stop();
        return;
    }

    reconhecimento = new SpeechRecognition();
    reconhecimento.lang = "pt-BR";
    reconhecimento.interimResults = false;

    reconhecimento.onstart = () => {
        gravando = true;
        botao.classList.add("gravando");
    };
    reconhecimento.onend = () => {
        gravando = false;
        botao.classList.remove("gravando");
    };
    reconhecimento.onresult = (evento) => {
        const texto = evento.results[0][0].transcript;
        document.getElementById("campo").value = texto;
        enviarTexto();
    };

    reconhecimento.start();
}
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return PAGINA


@app.route("/chat", methods=["POST"])
def chat():
    if not _cliente:
        return jsonify({"resposta": "Chave da IA nao configurada no servidor."})

    dados = request.get_json()
    mensagem = dados.get("mensagem", "").strip()
    if not mensagem:
        return jsonify({"resposta": "Nao recebi nenhuma mensagem."})

    historico_sessao = session.get("historico", [])
    historico_sessao.append({"role": "user", "content": mensagem})

    resposta = _cliente.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": SISTEMA}] + historico_sessao[-12:],
        max_tokens=500,
    )
    texto_resposta = resposta.choices[0].message.content
    historico_sessao.append({"role": "assistant", "content": texto_resposta})
    session["historico"] = historico_sessao[-20:]

    return jsonify({"resposta": texto_resposta})


@app.route("/imagem")
def imagem():
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
