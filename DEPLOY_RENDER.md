# NOVO GG — persistência e IA

## Render: não perder dados após deploy/restart
O projeto usa SQLite e uploads locais. Para manter tudo após atualização do serviço, crie um **Persistent Disk** no Render e monte em `/data`.

Use estas variáveis de ambiente:

- `CAMINHO_BD=/data/newggai.db`
- `PASTA_UPLOADS=/data/newgg_uploads`
- `FLASK_SECRET=<uma chave longa e aleatória>`
- `GOOGLE_CLIENT_ID=<se usar login Google>`
- `GROQ_API_KEY=<chave da Groq para o Amigo IA>`
- `GROQ_MODEL=llama-3.1-8b-instant` (opcional)

Sem Persistent Disk, o Render pode recriar o filesystem e os arquivos locais não são garantia de persistência.

## Amigo IA
A interface já possui o Amigo IA. Para ativar as respostas, configure `GROQ_API_KEY` no Render. A chave fica somente no servidor e não é enviada ao navegador.

## Publicação de perfis e servidores
Perfis e servidores públicos começam bloqueados. O administrador do site autoriza individualmente:

- perfil público de cada usuário;
- servidor público de cada servidor.

## Dono
`samuelgomeswx2000@gmail.com` é a conta dona: admin permanente e ID público `1`, independentemente do apelido usado no login Google.
