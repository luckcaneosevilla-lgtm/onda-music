# IVOX: APK Android e instalación en iPhone

Investigación: 28 de octubre de 2026. Solo documentación; no se instalaron herramientas, no se compilaron paquetes y no se cambió la aplicación. Antes de ejecutar estos pasos, unificar los arreglos de Claude y confirmar el dominio HTTPS definitivo.

## Estado comprobado del proyecto

`web/manifest.json` ya contiene `name` y `short_name` IVOX, `id`, `start_url` y `scope` `/`, `display: standalone`, colores e iconos PNG 192 y 512. Se comprobaron las dimensiones reales de ambos archivos con Pillow: 192×192 y 512×512. `web/index.html:6` enlaza el manifest y `web/pwa.js` registra el service worker en contexto seguro. El manifest solicitado no necesita cambios obligatorios. Revisar visualmente el margen seguro de los iconos antes de distribuir: declarar `maskable` no demuestra que el diseño tenga margen suficiente.

La PWA se ejecuta en el servidor; el APK no incluye Python, yt-dlp ni la base de datos. Tu hermana necesitará conexión y acceso al backend. `localhost` en su teléfono es su teléfono, no tu PC. Para una distribución sencilla usar HTTPS público con certificado válido; una URL privada de Tailscale exige configurar también su acceso a esa red.

## Android: camino recomendado desde Windows

Bubblewrap crea un contenedor Android basado en Trusted Web Activity: es una app instalable que muestra la PWA con el motor del navegador compatible, no una reescritura nativa. El proyecto pertenece a GoogleChromeLabs y Chrome lo documenta; su README aclara que no es un producto Google con soporte comercial. Node instalado aquí: **v24.19.0**. Docker no es obligatorio. [CLI de Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap/blob/main/packages/cli/README.md).

### 1. Confirmar el despliegue

Sustituye `https://TU-DOMINIO-IVOX` por la URL real, sin barra final. No es una URL desplegada por esta investigación.

```powershell
$ivoxOrigin = 'https://TU-DOMINIO-IVOX'
Invoke-WebRequest -UseBasicParsing "$ivoxOrigin/manifest.json"
Invoke-WebRequest -UseBasicParsing "$ivoxOrigin/icon-192x192.png"
Invoke-WebRequest -UseBasicParsing "$ivoxOrigin/icon-512x512.png"
Invoke-WebRequest -UseBasicParsing "$ivoxOrigin/service-worker.js"
```

Los cuatro deben devolver 200, con contenido correcto, sin login. Revisar en Chrome → DevTools → Application que el manifest no presente errores y que el service worker esté activo. La app debe arrancar en `/`, no en `/landing/`.

### 2. Inicializar el proyecto Android fuera del backend

```powershell
node --version
npm --version
npm install -g @bubblewrap/cli
New-Item -ItemType Directory -Path C:\ivox-android -Force
Set-Location C:\ivox-android
bubblewrap init --manifest "$ivoxOrigin/manifest.json"
```

En el asistente: aceptar la instalación gestionada de JDK y Android SDK; confirmar nombre **IVOX**, host del despliegue, ruta `/`, icono 512, paquete propuesto **com.lucascaneo.ivox** y modo standalone. Elige una clave nueva de firma, por ejemplo `C:\ivox-android\ivox-release.keystore`, alias `ivox`, y contraseñas propias. Guardar una copia privada del keystore y sus contraseñas para futuras actualizaciones. No subirlos al repositorio ni mandarlos con el APK. Si se configura Java manualmente, seguir la versión del README actual (JDK 17), sin sustituirla automáticamente por cualquier Java ya instalado.

### 3. Compilar

```powershell
bubblewrap doctor
bubblewrap build
Get-Item .\app-release-signed.apk
Get-FileHash .\app-release-signed.apk -Algorithm SHA256
```

Aceptar las licencias del SDK cuando se soliciten e introducir las contraseñas de firma de forma interactiva. No usar `--skipSigning`. El archivo para instalar es **app-release-signed.apk**, no el AAB. Las claves deben mantenerse iguales entre versiones. [Guía de TWA de Chrome](https://developer.chrome.com/docs/android/trusted-web-activity/quick-start).

### 4. Vincular dominio y APK: imprescindible para quitar la barra del navegador

Obtener la huella **SHA-256 del certificado de firma**, distinta del SHA-256 del archivo APK calculado arriba. Con el JDK instalado por Bubblewrap, localizar `jdkPath` en `$env:USERPROFILE\.bubblewrap\config.json`:

```powershell
$ivoxBwConfig = Get-Content "$env:USERPROFILE\.bubblewrap\config.json" -Raw | ConvertFrom-Json
& (Join-Path $ivoxBwConfig.jdkPath 'bin\keytool.exe') -list -v -keystore .\ivox-release.keystore -alias ivox
bubblewrap fingerprint add 'PEGA:AQUI:LA:HUELLA:SHA256:DEL:CERTIFICADO'
bubblewrap fingerprint generateAssetLinks
```

Comprobar el resultado `assetlinks.json`: paquete `com.lucascaneo.ivox`, huella del certificado local y relación `delegate_permission/common.handle_all_urls`. Publicarlo como **https://TU-DOMINIO-IVOX/.well-known/assetlinks.json**, accesible sin autenticación, HTTP 200, `Content-Type: application/json`, sin redirección. Para este APK distribuido directamente se usa el certificado local, no una futura clave de Play App Signing. [Asociaciones de Android](https://developer.android.com/training/app-links/configure-assetlinks) y [comando de huellas, código oficial](https://raw.githubusercontent.com/GoogleChromeLabs/bubblewrap/main/packages/cli/src/lib/cmds/fingerprint.ts).

**Ya implementado en el servidor:** `/.well-known/assetlinks.json` tiene ruta propia, sin login. Sirve el contenido de la variable `ANDROID_ASSETLINKS` (JSON en una línea) o, si no existe, el archivo `data/assetlinks.json` del servidor (fuera de git: la huella es configuración del despliegue, no código). Sin ninguno de los dos responde **404 JSON**, nunca el HTML de la app; un JSON inválido no se publica. Tras generarlo:

```powershell
Copy-Item .\assetlinks.json C:\ruta\de\IVOX\data\assetlinks.json   # o define ANDROID_ASSETLINKS en .env
Invoke-RestMethod "$ivoxOrigin/.well-known/assetlinks.json"        # debe mostrar tu package_name y huella
```

Comprobar que la respuesta contiene la huella correcta. Reconstruir si se cambia el proyecto Android y probar en el teléfono: sin validación puede abrir como Custom Tab con barra del navegador.

### 5. Instalar y compartir

Primero probar en un Android propio, con Chrome actualizado. Con depuración USB habilitada y autorización al PC, se puede usar `bubblewrap install` desde `C:\ivox-android`.

Después enviar **solo el APK firmado** por WhatsApp como documento. En el teléfono receptor: guardar el documento, abrirlo desde Archivos/Descargas y autorizar “Instalar aplicaciones desconocidas” a la aplicación que inicia la instalación, si Android lo pide. Los nombres de los menús varían por fabricante. No es necesario publicar en Play Store. Si WhatsApp bloquea el archivo o la apertura, compartir un enlace HTTPS de descarga; no depender de que todas sus versiones manejen APK igual. El APK no hace que la música funcione offline ni elimina restricciones del proveedor.

**Condición actual de Android:** Google está desplegando verificación de desarrolladores también fuera de Play. La fase regional anunciada desde septiembre de 2026 afecta a Brasil, Indonesia, Singapur y Tailandia, con expansión prevista desde 2027. Pueden aparecer requisitos de verificación o el flujo avanzado para apps no registradas; ADB sigue disponible. Para uso entre pocos conocidos, consultar también las cuentas de distribución limitada. No prometer que el único paso será “permitir origen desconocido” en todos los países. [Calendario oficial](https://android-developers.googleblog.com/2026/03/android-developer-verification-rolling-out-to-all-developers.html) y [FAQ vigente](https://developer.android.com/developer-verification/guides/faq).

### Alternativa Docker, no necesaria para empezar

Existe la imagen mantenida por el proyecto `ghcr.io/googlechromelabs/bubblewrap:latest`. Con Docker Desktop en contenedores Linux, iniciar desde una carpeta de salida dedicada:

```powershell
Set-Location C:\ivox-android
docker run --rm -it --mount "type=bind,source=$($PWD.Path),target=/work" --workdir /work ghcr.io/googlechromelabs/bubblewrap:latest init --manifest "$ivoxOrigin/manifest.json"
docker run --rm -it --mount "type=bind,source=$($PWD.Path),target=/work" --workdir /work ghcr.io/googlechromelabs/bubblewrap:latest build
```

Usar un keystore persistente bajo `/work`, no en una carpeta efímera del contenedor. Elegir **Node o Docker para un mismo proyecto**; las rutas de keystore/JDK de Windows no son rutas Linux. Los comandos Docker son una alternativa documentada, no ejecutada aquí; la ruta Node es la propuesta principal.

## iPhone: instalar una IPA no es compilarla

| Opción | Desde Windows | Apple ID gratuito | Mantenimiento |
|---|---|---|---|
| AltStore Classic + AltServer | Sí, instala una IPA ya compilada | Sí | Renovar antes de 7 días; máximo 3 apps activas, incluyendo AltStore |
| Sideloadly | Sí, firma e instala una IPA ya compilada | Sí | Renovar antes de 7 días; máximo 3 apps activas |
| PWA desde Safari | Sí, basta una URL accesible | No necesita firma | Sin renovación semanal ni cupo de apps firmadas |

Los 3 son apps instaladas simultáneamente con perfiles gratuitos, no “3 instalaciones en total”. Extensiones pueden consumir App IDs adicionales; AltStore documenta un límite separado de 10 App IDs por semana. No confundir AltStore Classic con el marketplace AltStore PAL. [AltStore: apps activas](https://faq.altstore.io/altstore-classic/activating-apps), [App IDs](https://faq.altstore.io/altstore-classic/app-ids), [Sideloadly FAQ](https://sideloadly.io/faq.html).

**AltStore Classic:** instalar AltServer para Windows y los componentes Apple según su guía; conectar el iPhone por USB, confiar en el PC, habilitar sincronización Wi-Fi, usar “Install AltStore”, autenticarse con el Apple ID, confiar en el perfil y activar Developer Mode cuando iOS lo requiera. En AltStore, importar la IPA y renovar con AltServer accesible. AltStore ocupa una plaza; quedan normalmente dos para otras apps. [Pasos oficiales Windows](https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows).

**Sideloadly:** descargarlo de `sideloadly.io`, instalar los componentes Apple indicados, conectar y confiar por USB, elegir el dispositivo, arrastrar la IPA, introducir el Apple ID y ejecutar la instalación. Confiar en el perfil y activar Developer Mode si se solicita. Su daemon puede renovar con el PC encendido y el dispositivo accesible por USB o Wi-Fi configurado; no elimina el vencimiento del perfil. [Instalador oficial](https://sideloadly.io/index.html).

**¿Se puede generar nuestra IPA sin tener un Mac?** No con Bubblewrap ni con Docker Desktop Linux por sí solos. IVOX todavía no tiene proyecto iOS. Una vía sería un contenedor WKWebView con Capacitor, pero el toolchain soportado requiere macOS y Xcode; un Mac remoto o un servicio de compilación puede reemplazar tener un Mac propio, con disponibilidad y coste aparte. Una IPA no firmada compilada en macOS podría firmarse posteriormente desde Windows con estas herramientas, si sus capacidades lo permiten; no se ha generado ni comprobado aquí. No asumir que un servicio cloud acepte firma gratuita ni que produzca una IPA instalable sin configurar certificados. [Requisitos de Capacitor](https://capacitorjs.com/docs/getting-started/environment-setup).

**Recomendación para iPhone: conservar la PWA.** Abrir la URL HTTPS en Safari → Compartir (o menú de acciones, según versión) → Añadir a pantalla de inicio → activar “Abrir como app web” si aparece → Añadir. No requiere tienda, Mac, cuota de apps ni renovación a los 7 días. Sigue dependiendo de red y de las capacidades reales del navegador; instalarla no añade offline o audio en segundo plano garantizado. [Guía de Apple](https://support.apple.com/guide/iphone/bookmark-a-website-iph42ab2f3a7/ios).

## Archivos pendientes, sin modificar ahora

- `web/manifest.json`: **revisado**. Iconos separados en `any` y `maskable` (combinarlos en un solo `purpose` está desaconsejado); se comprobó con Pillow que ningún píxel dorado del icono de 512 sale de la zona segura del 80 %. Añadidos `description`, `categories`, `orientation`, `display_override` y accesos directos (Buscar, Biblioteca), que Bubblewrap usa en el lanzador.
- `/.well-known/assetlinks.json`: **ruta hecha** (ver paso 4); falta solo el archivo con la firma real.
- `.gitignore` y `.dockerignore`: **excluyen ya** `*.keystore`, `*.jks`, `twa-manifest.json` y `*.apk`/`*.aab`. Propuesta actual: `C:\ivox-android`, fuera del backend.
- Proyecto Android generado: `twa-manifest.json`, host, packageId y firma; no toca el backend de reproducción.
- Proyecto iOS: no recomendado en esta fase; no existe aún.

Pendientes: confirmar dominio, compilar APK, publicar la asociación, instalar en el teléfono receptor y verificar reproducción/seek. No se han probado APK, IPA ni teléfonos físicos en esta investigación.
