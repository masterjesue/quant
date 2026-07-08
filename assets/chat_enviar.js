// Enter envia a mensagem do Mentor IA, Shift+Enter quebra linha — padrão de
// chat moderno (Slack/Discord). Necessário desde que o campo virou
// dcc.Textarea (2026-07-08, pra mostrar mais texto visível enquanto digita)
// — Textarea não tem a prop `n_submit` que dcc.Input tinha, então o Enter
// simples precisa ser interceptado aqui e redirecionado pro clique do botão.
// Delegado em document (não no elemento) porque roda antes do primeiro
// render do Dash sem precisar esperar o DOM montar.
document.addEventListener('keydown', function (e) {
    if (e.target && e.target.id === 'chat-input' && e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        var btn = document.getElementById('btn-chat-enviar');
        if (btn) btn.click();
    }
});
