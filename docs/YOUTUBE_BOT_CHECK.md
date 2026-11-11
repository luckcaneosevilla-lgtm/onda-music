# YouTube `bot_check`: defensa en capas

`bot_check` ("Sign in to confirm you're not a bot") es la reputación de la IP de
datacenter del servidor, no un fallo de la app. El 2026-11-11 (6.0) el mismo
código reprodujo 5/5 canciones y una hora después 0/5. El servidor ya prueba tres
clientes InnerTube (VISIONOS, ANDROID_VR, TVHTML5) y después yt-dlp.

## Capa 1 — PO tokens (bgutil)

Código: `audio_resolver.py` (`POT_PROVIDER_URL`, `pot_available()`), plugin
`bgutil-ytdlp-pot-provider==2.0.2` en `requirements.txt`; el `Dockerfile` falla si
el plugin no está instalado.

- Con `POT_PROVIDER_URL` definida y el proveedor respondiendo a `/ping`, yt-dlp
  prueba primero los clientes `mweb` y `android_vr` con PO token y después los de
  siempre (visionos, por defecto, tv/web_safari/mweb).
- El estado del proveedor se comprueba como mucho una vez por minuto (1 s de
  espera). Caído, se omite el intento con token: medido en local, un proveedor
  inalcanzable sin esta comprobación hacía tardar la extracción 8,7 s; con ella,
  2,4–3,8 s.
- Los registros muestran `{"event":"pot_provider","status":"up"|"down"}` cuando cambia.
- InnerTube directo no cambia de cliente: ANDROID_VR devolvía `LOGIN_REQUIRED`
  para Feid y VISIONOS sí entregaba audio (docs/INNERTUBE_AUDIO.md). Los PO tokens
  solo los usa yt-dlp.

### Pasos en Northflank

1. **Nuevo servicio** → *Deployment* → imagen externa
   `brainicism/bgutil-ytdlp-pot-provider:2.0.2` (misma versión mayor que el plugin;
   si esa etiqueta no existe, `latest`).
2. **Puertos**: añade el puerto `4416`, protocolo HTTP, **sin** exponerlo
   públicamente (solo red privada del proyecto).
3. Anota el nombre del servicio (p. ej. `bgutil-provider`).
4. En el servicio **onda-music** → *Environment* → añade
   `POT_PROVIDER_URL=http://bgutil-provider:4416` (con el nombre real del paso 3).
5. Reinicia onda-music. En los registros debe aparecer
   `"event": "pot_provider", "status": "up"` tras la primera canción.

## Capa 2 — Cookies de una cuenta secundaria

Código: `IVOX_YTDLP_COOKIES` (contenido) o `IVOX_YTDLP_COOKIES_FILE` (ruta). Se
copian a un archivo temporal privado (0600) porque yt-dlp reescribe las cookies
renovadas y los secretos montados son de solo lectura. Su contenido nunca se
registra. `cookies.txt` está en `.gitignore`.

1. Crea o usa una cuenta de Google **secundaria y desechable**; YouTube puede
   bloquear la cuenta cuyas cookies usa un servidor.
2. En una ventana privada del navegador, inicia sesión en youtube.com con ella,
   exporta las cookies en formato Netscape (extensión tipo "Get cookies.txt
   LOCALLY") y cierra esa ventana sin cerrar sesión.
3. En Northflank → onda-music → *Environment* → secreto `IVOX_YTDLP_COOKIES` con
   el contenido completo del archivo (vale pegado con saltos de línea reales o con
   `\t`/`\n` literales).
4. Caducidad: si YouTube rechaza las cookies, los registros muestran
   `{"event":"ytdlp_cookies","status":"rejected",...}` (como mucho cada 10 minutos).
   Al arrancar, `"status":"loaded"` confirma que se leyeron. Exporta unas nuevas y
   sustituye el secreto.

## Capa 3 — Runtime JavaScript (Deno)

Ya estaba: el `Dockerfile` copia Deno 2.3.3 y falla la construcción si yt-dlp no
puede usarlo (`yt_dlp_ejs`).

## Capa 4 — Ritmo de peticiones

- `--force-ipv4` siempre (`source_address=0.0.0.0`).
- `--sleep-requests` es **opcional** (`IVOX_YTDLP_SLEEP_REQUESTS`, máximo 2 s): cada
  extracción hace varias peticiones a YouTube y el límite de extracción es de 9 s,
  así que 1 s por petición haría fallar Play por tiempo.
- `--sleep-interval`/`--max-sleep-interval` no se aplican: solo afectan a descargas
  y aquí yt-dlp solo extrae la URL (`download=False`).

## Capa 5 (plan B, de pago) — Proxy residencial

Si con las capas 1–4 sigue apareciendo `bot_check`, la opción garantizada es sacar
el tráfico hacia YouTube por IPs residenciales. **No está implementado.**

- Proveedores: IPRoyal, Bright Data (también Oxylabs, Smartproxy/Decodo). Cobran por
  GB transferido; consulta su precio actual.
- Coste a tener en cuenta: con la ruta nativa el servidor transmite todo el audio,
  así que pasaría por el proxy todo el audio, no solo la extracción (~3–4 MB por canción en AAC).
  Para limitarlo, usar el proxy solo en la extracción (opción `proxy` de yt-dlp) y
  comprobar si YouTube acepta la descarga desde la IP del servidor.
- Implementación prevista: variable `IVOX_YTDLP_PROXY` → `OPTIONS['proxy']`.
