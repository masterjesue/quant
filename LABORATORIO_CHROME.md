# Laboratório Chrome — ponte de contexto entre 2 instâncias de Claude Code

Jesué roda DUAS instâncias de Claude Code na MESMA pasta do projeto
(`c:\Users\jesue\OneDrive\Documentos\Trade\quant`): esta (o "laboratório de
programação", que edita o código) e uma instância nativa separada que usa
uma extensão de Chrome pra testar o Super Quant ao vivo no navegador. As
duas conversas não compartilham contexto automaticamente — mas como AMBAS
são Claude Code com acesso de arquivo total à mesma pasta, este arquivo é
lido/escrito DIRETO por qualquer uma delas, sem o Jesué precisar copiar ou
colar nada.

**Se você é a instância que está testando via Chrome**: antes de testar,
leia a seção "Contexto atual" abaixo pra saber o que mudou recentemente e o
que vale focar. Depois de testar, use sua ferramenta de edição pra escrever
os achados direto na seção "Feedback de testes pendente" — pode ser
informal (o que você clicou, o que esperava, o que aconteceu), não precisa
ser um relatório técnico.

**Se você é a instância que programa (laboratório)**: quando o Jesué pedir
pra ver o feedback (ex: "veja o feedback no LABORATORIO_CHROME.md"), leia a
seção "Feedback de testes pendente", investigue e implemente as correções
no código. Depois de processar cada item, mova ele pra "Feedback já
processado" com uma nota curta do que foi feito — evita reprocessar o mesmo
achado duas vezes. Mantenha "Contexto atual" atualizado depois de mudanças
relevantes, é o que permite a outra instância se orientar sem o Jesué
reexplicar o projeto toda vez.

---

## Contexto atual (mantido pelo Claude Code — última atualização: 2026-07-21)

**O que é o Super Quant**: dashboard de day trading quantitativo (WIN/WDO,
M5) com 2 skins (`dashboard.py` clássico porta 8050, `dashboard_98.py` estilo
Windows 98 porta 8051) + módulo Diário de Trade em processos separados
(`diario_app_classico.py` porta 8053, `diario_app_98.py` porta 8052).

**Trabalho recente desta sessão (2026-07-21), tudo já testado e no ar:**
- Modo Aprendizagem: as 4 fases do plano original completas (switch,
  snapshot estendido, ajuste fino log-only, sugestões estruturais, Big
  Players condensado no hero).
- 3 bugs reais de hedge corrigidos na reconciliação com o MT5 (posições que
  fecham via ordem oposta — a conta é hedging, não netting): metades órfãs
  de hedge geravam pendências fantasma com saldo R$0,00; fechamentos via
  `DEAL_ENTRY_OUT_BY` também zeravam o saldo mesmo com preço real; e o
  cache de pendências era apagado inteiro toda vez que o MT5 caía (a
  conexão é instável, cai e volta sozinha).
- Botão "Editar" de uma pendência no Histórico agora abre um pop-up de
  verdade (reaproveitando o formulário da aba Posição), pré-carregado com
  o que o MT5 capturou — antes só trocava de aba sem carregar nada.
- Fechamento de posição (par de hedge ou avulso) agora aparece no
  Histórico automaticamente como pendência, unificado com as descobertas
  antigas da reconciliação — antes só aparecia na aba Posição.
- Bug de fronteira corrigido no slider "Sentimento de Mercado" do Diário:
  o valor 75 (marca "Ganância") classificava errado como "Ganância
  Extrema" — corrigido.

**O que vale testar agora, especificamente:**
- Diário → Histórico → clicar "Editar" numa linha PENDENTE → conferir se o
  pop-up abre com os dados certos (ativo/preço/volume) e se "Finalizar e
  gravar no Diário" fecha o pop-up e grava certo.
- Diário → Posição → conferir se pares de hedge aparecem mesclados (1 card
  só, não 2) e se fechar um deles não deixa duplicata em nenhum lugar.
- Slider "Sentimento de Mercado" (Novo Trade e Posição) — arrastar até cada
  marca (Medo Extremo/Medo/Neutro/Ganância/Ganância Extrema) e conferir se
  o rótulo bate com a marca.
- Modo Aprendizagem (Configurações) — ligar o switch, rodar recalibração,
  conferir se não trava nada.

**Cuidado ao testar**: o MT5 cai e reconecta sozinho com frequência (achado
repetido nesta sessão) — se algo parecer "não faz nada", tente de novo antes
de reportar como bug; pode ser só instabilidade momentânea da conexão, não o
código. Evite clicar em "Finalizar e gravar no Diário" com dados de teste —
isso grava um trade de verdade no Diário (não tem modo sandbox).

---

## Feedback de testes pendente (o Claude Code lê e processa daqui)

*(vazio no momento — cole aqui o que a conversa do Chrome encontrar)*

---

## Feedback já processado (histórico, não precisa reler)

*(nada ainda)*
