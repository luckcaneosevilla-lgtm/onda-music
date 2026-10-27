const recoveryToken=location.hash.slice(1);
history.replaceState(null,'','/recover');
const form=document.querySelector('#recovery-form'),result=document.querySelector('#result');
if(!/^[A-Za-z0-9_-]{43}$/.test(recoveryToken)){
  form.hidden=true;result.textContent='Necesitas un enlace de recuperación local válido. Genera otro desde la terminal del equipo que ejecuta ONDA.';
}
form.onsubmit=async event=>{
  event.preventDefault();
  const password=document.querySelector('#password').value;
  if(password!==document.querySelector('#confirmation').value){result.textContent='Las contraseñas no coinciden.';return}
  const button=form.querySelector('button');button.disabled=true;result.textContent='Guardando tu nueva contraseña…';
  try{
    const response=await fetch('/api/recover-admin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:recoveryToken,password})});
    const data=await response.json();if(!response.ok)throw Error(data.error);
    form.reset();location.replace('/admin');
  }catch(error){result.textContent=error.message;button.disabled=false}
};
