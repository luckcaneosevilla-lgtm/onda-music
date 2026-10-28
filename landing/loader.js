'use strict';
const pause = document.getElementById('pause');
pause.onclick = () => {
  const paused = document.body.classList.toggle('paused');
  pause.textContent = paused ? 'Continuar' : 'Pausar';
  pause.setAttribute('aria-pressed', String(paused));
};
document.getElementById('fullscreen').onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
    else document.getElementById('fullscreen').textContent = 'Usa pantalla vertical';
  } catch { document.getElementById('fullscreen').textContent = 'No disponible aquí'; }
};
const controls = document.querySelector('.controls');
const restore = document.querySelector('.restore');
document.getElementById('clean').onclick = () => { controls.hidden = true; controls.style.display = 'none'; restore.hidden = false; restore.focus({preventScroll:true}); restore.blur(); };
restore.onclick = () => { controls.hidden = false; controls.style.display = ''; restore.hidden = true; };
document.addEventListener('keydown', event => { if (event.key === 'Escape') restore.click(); });
