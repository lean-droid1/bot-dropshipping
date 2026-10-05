"""
Bot de Dropshipping — Tienda Negocio / RXZ Web
Versión limpia con API WooCommerce del proveedor.
"""
import os, time, json, re, io, threading, imaplib, email
try:
    from flask import Flask, request, jsonify
    FLASK_OK = True
except ImportError:
    FLASK_OK = False
    print('⚠️ Flask no instalado — webhooks desactivados')

try:
    from curl_cffi import requests as cf_requests
    CURL_CFFI_OK = True
    print('✅ curl-cffi disponible — bypass Cloudflare activo')
except ImportError:
    cf_requests = None
    CURL_CFFI_OK = False
    print('⚠️ curl-cffi no instalado — usando requests normal')
from email.header import decode_header
from datetime import datetime, timedelta, timezone
import requests
urllib3_imported = False
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    urllib3_imported = True
except Exception:
    pass

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
DB_FILE          = "estado_productos.json"
API_BASE         = "https://developers.tiendanegocio.com/v1"
PROV_API         = "https://rxzweb.com/wp-json/wc/store/v1/products"
USER_AGENT       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ── ComerciApp (web nueva de Leandro) — destino de la sincronización ──────────
# El bot escribe el catálogo del proveedor acá vía API key (header X-Bot-Key).
COMERCIAPP_API   = (os.environ.get("COMERCIAPP_API") or "").strip().rstrip("/")  # ej: https://sistema-unificado-api-production.up.railway.app
COMERCIAPP_KEY   = (os.environ.get("BOT_API_KEY") or "").strip()                 # la misma BOT_API_KEY del backend
COMERCIAPP_SECCION = (os.environ.get("COMERCIAPP_SECCION_ID") or "").strip()     # opcional: id de la sección DEPOSITO (si no, el backend la busca)

# ── Categorías del proveedor a EXCLUIR (no cargar ni avisar) ──────────────────
# Se comparan en minúsculas y por coincidencia parcial. Editable por Telegram con
# /excluir_categoria y /incluir_categoria (se guarda en la DB del bot).
CATEGORIAS_EXCLUIDAS_DEFAULT = ['modulos', 'módulos', 'baterias', 'baterías']

# Márgenes y alertas (configurables via Railway Variables)
MARGEN           = float(os.environ.get("MARGEN", "0.90"))   # /0.90 = ~11% de ganancia
ALERTA_STOCK       = 3       # Alerta Telegram cuando stock llega a este numero
ENVIO_GRATIS_MIN   = 100000  # Activar envio gratis en productos >= este precio

# Mesas pesadas (no envio gratis): RT-01D:3643182, RF-RT02D:3643163, LW-A1:3643153, LW-A1 Mini:3643173
# Estos productos tienen envío caro (~$200.000), se les pone cartel manual en la foto
PRODUCTOS_PESADOS_IDS = {3643182, 3643163, 3643153, 3643173}

CICLO_MINUTOS    = int(os.environ.get("CICLO_MINUTOS", "15") or 15)       # de día, cada cuántos minutos monitorea
CICLO_NOCHE_MIN  = int(os.environ.get("CICLO_NOCHE_MIN", "60") or 60)     # de noche (menos cambios), cada cuántos minutos
NOCHE_DESDE      = int(os.environ.get("NOCHE_DESDE", "21") or 21)          # hora AR en que arranca la "noche"
NOCHE_HASTA      = int(os.environ.get("NOCHE_HASTA", "8") or 8)            # hora AR en que vuelve el ritmo de día
THORDATA_USD_1K  = float(os.environ.get("THORDATA_USD_1K", "1.30") or 1.30)  # precio por 1.000 pedidos del Web Unlocker (para estimar el gasto)

# Categorías del proveedor a excluir siempre (no nos interesan)
CATEGORIAS_EXCLUIDAS = {'pantallas', 'modulos', 'baterias', 'bateria'}

PALABRAS_INTERES = [
    'ma ant','amaoe','2uul','goot wick','mijing','louwei','rf4','jakemy',
    'kailiwei','kslid','aifen','sugon','jcid','jc','v1','v1s','v1se',
    'v1 pro','programadora','organizador','cinta','silla','mesa','puas',
    'hilo','cepillo','flux','malla','estaño','pinza','alicate','tweezer',
    'brusela','stencil','pasta','mascara','ventosa','rodillo','soporte',
    'holder','microscopio','fuente','estacion','plancha','precalentadora',
    'autoclave','cabina','compresor','camara','detector','tester','probador'
]

# ── Variables de entorno ──────────────────────────────────────────────────────
def _e(k): return (os.environ.get(k) or "").strip() or None

TELEGRAM_TOKEN = _e("TELEGRAM_TOKEN")
CHAT_ID        = _e("CHAT_ID")
GMAIL_USER     = _e("GMAIL_USER")
GMAIL_PASS     = _e("GMAIL_PASS")
CLIENT_ID      = _e("CLIENT_ID")
CLIENT_SECRET  = _e("CLIENT_SECRET")
NOMBRE_TIENDA  = _e("NOMBRE_TIENDA") or "🧪 PRUEBA"

# ── Compras automáticas al proveedor ─────────────────────────────────────────
PROV_USER          = _e("PROV_USER")
WEBHOOK_BASE_URL   = _e("WEBHOOK_BASE_URL")
GROQ_API_KEY       = _e("GROQ_API_KEY")
WEBSHARE_PROXY     = _e("WEBSHARE_PROXY")
WEBSHARE_USER      = _e("WEBSHARE_USER")
WEBSHARE_PASS      = _e("WEBSHARE_PASS")

# ── Proxy residencial DataImpulse (bypass Cloudflare definitivo) ─────────────
PROXY_HOST         = _e("PROXY_HOST")      # gw.dataimpulse.com
PROXY_PORT         = _e("PROXY_PORT")      # 823
PROXY_USER         = _e("PROXY_USER")
PROXY_PASS         = _e("PROXY_PASS")

def _get_residential_proxy(sess=None):
    """Arma URL del proxy residencial si las credenciales están configuradas.
    sess: si se pasa, fuerza una IP nueva de ThorData (rotacion por intento)."""
    if PROXY_HOST and PROXY_PORT and PROXY_USER and PROXY_PASS:
        user = PROXY_USER
        if sess and "-sessid-" not in user:
            user = f"{user}-sessid-{sess}"
        return f"http://{user}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
    return None

PROV_PASS      = _e("PROV_PASS")
CUIT_PROVEEDOR = _e("CUIT_PROVEEDOR")
PROV_LOGIN_URL = "https://rxzweb.com/wp-login.php"
PROV_CART_URL  = "https://rxzweb.com/wp-json/wc/store/v1/cart"
PROV_CHKOUT_URL= "https://rxzweb.com/wp-json/wc/store/v1/checkout"

# ── Token API Tienda Negocio ──────────────────────────────────────────────────
_token    = None
_store_id = None

def cargar_token():
    global _token, _store_id
    t = _e("API_TOKEN"); u = _e("API_USER_ID")
    if t and u:
        _token = t; _store_id = u
        print(f"✅ Token desde Railway (store_id={u})"); return
    db = leer_db()
    if db.get("api_token") and db.get("api_user_id"):
        _token = db["api_token"]; _store_id = db["api_user_id"]
        print(f"✅ Token desde DB (store_id={_store_id})")

def guardar_token(token, store_id):
    global _token, _store_id
    _token = token; _store_id = store_id
    db = leer_db(); db["api_token"] = token; db["api_user_id"] = store_id
    escribir_db(db); print(f"💾 Token guardado (store_id={store_id})")

# ══════════════════════════════════════════════════════════════════════════════
# BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════
def leer_db():
    for intento in range(3):
        if not os.path.exists(DB_FILE): break
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                d.setdefault("productos_proveedor", {})
                d.setdefault("sincronizados", {})
                d.setdefault("pedidos_procesados", [])
                d.setdefault("ofertas_pendientes", {})
                d.setdefault("ordenes", {})
                d.setdefault("variantes_cache", {})
                d.setdefault("pids_refrescar", [])
                d.setdefault("ultimo_orden_id", 0)
                d.setdefault("scraping_activo", False)  # arranca APAGADO tras cada deploy (evita bloqueo RXZ)
                d.setdefault("categorias_excluidas", CATEGORIAS_EXCLUIDAS_DEFAULT)
                d.setdefault("categorias_conocidas", [])  # para detectar categorías nuevas del proveedor
                return d
        except Exception:
            time.sleep(0.3)   # otro hilo la estaba escribiendo: reintentar antes de devolver una DB vacía
    return {"productos_proveedor":{}, "sincronizados":{}, "pedidos_procesados":[], "ofertas_pendientes":{}, "ordenes":{}}

def escribir_db(d):
    """Escritura atómica (archivo temporal + reemplazo): otro hilo nunca lee una DB a medio escribir."""
    try:
        tmp = f"{DB_FILE}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DB_FILE)
    except Exception as e: print(f"❌ DB: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════
def _partir(msg, n=3900):
    """Telegram corta en 4096 caracteres: parte el texto por líneas."""
    if len(msg) <= n: return [msg]
    partes, actual = [], ""
    for linea in msg.split("\n"):
        while len(linea) > n:
            if actual: partes.append(actual); actual = ""
            partes.append(linea[:n]); linea = linea[n:]
        if actual and len(actual) + len(linea) + 1 > n:
            partes.append(actual); actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual: partes.append(actual)
    return partes

def tg(msg, silencioso=None):
    """Manda un mensaje. De noche (SILENCIO_DESDE a SILENCIO_HASTA) llega sin sonido.
    Si el formato rompe (un nombre con * o _), lo reenvía como texto plano en vez de perderlo."""
    if not msg or not TELEGRAM_TOKEN or not CHAT_ID: return
    if silencioso is None: silencioso = _es_silencio()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for parte in _partir(msg):
        try:
            r = requests.post(url, json={"chat_id": CHAT_ID, "text": parte, "parse_mode": "Markdown",
                                         "disable_notification": bool(silencioso)}, timeout=15)
            if r.status_code == 400 and "parse" in (r.text or "").lower():
                requests.post(url, json={"chat_id": CHAT_ID, "text": parte, "disable_notification": bool(silencioso)}, timeout=15)
        except Exception as e: print(f"❌ Telegram: {e}")

def tg_doc(data, nombre, caption=""):
    if not TELEGRAM_TOKEN or not CHAT_ID: return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"document": (nombre, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=30)
        return r.status_code == 200
    except Exception as e: print(f"❌ Telegram doc: {e}"); return False

def _nt(txt): return f"[{NOMBRE_TIENDA}] {txt}"

COMANDOS_TG = [
    ("menu", "Panel de control con botones"),
    ("estado", "Cómo está el bot: último ciclo, créditos y avisos"),
    ("encender", "Encender la sincronización con el proveedor"),
    ("apagar", "Apagar la sincronización"),
    ("ciclo", "Correr un ciclo ahora"),
    ("test_proxy", "Probar si se puede leer el proveedor (directo y con proxy)"),
    ("pendientes", "Productos nuevos esperando aprobación"),
    ("reparar_fotos", "Subir a la web las fotos rotas del proveedor"),
    ("ver_categorias", "Categorías del proveedor (cargadas y excluidas)"),
    ("ayuda", "Todos los comandos"),
]

def registrar_comandos_tg():
    """Carga la lista de comandos que Telegram sugiere al escribir / (reemplaza la vieja)."""
    if not TELEGRAM_TOKEN: return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands", timeout=15,
                          json={"commands": [{"command": c, "description": d} for c, d in COMANDOS_TG]})
        print(f"📋 Comandos de Telegram: {'OK' if r.status_code == 200 else r.text[:120]}")
    except Exception as e: print(f"⚠️ setMyCommands: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# ESTADO DEL BOT, ALERTAS Y HORARIOS
# ══════════════════════════════════════════════════════════════════════════════
# El estado va en un archivo aparte de la DB: el ciclo reescribe la DB entera al terminar
# y se pisarían las alertas que se registran mientras corre.
ESTADO_FILE      = "estado_bot.json"
_estado_lock     = threading.RLock()
ALERTA_REPETIR_H = float(os.environ.get("ALERTA_REPETIR_H", "6") or 6)   # si una falla sigue, se repite el aviso cada X horas
SILENCIO_DESDE   = int(os.environ.get("SILENCIO_DESDE", "0") or 0)       # hora AR: de noche los avisos llegan sin sonido
SILENCIO_HASTA   = int(os.environ.get("SILENCIO_HASTA", "8") or 8)
RESUMEN_HORA     = int(os.environ.get("RESUMEN_HORA", "9") or 9)         # hora AR del resumen diario
SUBA_ALERTA_PCT  = float(os.environ.get("SUBA_ALERTA_PCT", "15") or 15)  # subas del proveedor desde este % se avisan aparte
APAGADO_AVISO_H  = float(os.environ.get("APAGADO_AVISO_H", "2") or 2)    # recordar si el scraping quedó apagado más de X horas

def _ahora_ar():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)

def _es_silencio(h=None):
    h = _ahora_ar().hour if h is None else h
    a, b = SILENCIO_DESDE, SILENCIO_HASTA
    if a == b: return False
    return a <= h < b if a < b else (h >= a or h < b)

def _dur(seg):
    m = int(max(0, seg) // 60)
    if m < 60: return f"{max(m, 1)} min"
    if m < 1440: return f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h"
    return f"{m // 1440} d {(m % 1440) // 60} h"

def _hora_ar(ts):
    return (datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None) - timedelta(hours=3)).strftime("%H:%M")

def leer_estado():
    with _estado_lock:
        try:
            with open(ESTADO_FILE, "r", encoding="utf-8") as f: d = json.load(f)
        except Exception: d = {}
        d.setdefault("alertas", {}); d.setdefault("dia", {}); d.setdefault("ultimo_ciclo", {})
        return d

def _mod_estado(fn):
    """Lee, modifica y guarda el estado de una vez (lo tocan varios hilos). Devuelve lo que devuelva fn."""
    with _estado_lock:
        d = leer_estado()
        r = fn(d)
        try:
            with open(ESTADO_FILE + ".tmp", "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(ESTADO_FILE + ".tmp", ESTADO_FILE)
        except Exception as e: print(f"❌ estado: {e}")
        return r

def alerta(clave, titulo, detalle="", grave=True):
    """Avisa una falla UNA vez. Si sigue, la repite cada ALERTA_REPETIR_H horas (no cada ciclo).
    Cuando se arregla, resolver(clave) avisa que volvió a andar."""
    ahora = time.time()
    def _f(d):
        a = d["alertas"].get(clave)
        if a and ahora - a.get("aviso", 0) < ALERTA_REPETIR_H * 3600:
            a["detalle"] = detalle; return None
        desde = a["desde"] if a else ahora
        d["alertas"][clave] = {"titulo": titulo, "detalle": detalle, "desde": desde, "aviso": ahora, "grave": grave}
        return desde
    desde = _mod_estado(_f)
    if desde is None: return
    print(f"🚨 Alerta {clave}: {titulo}")
    sigue = f"\nSigue pasando desde hace {_dur(ahora - desde)}." if ahora - desde > 120 else ""
    tg(f"{'🚨' if grave else '⚠️'} *{_nt(titulo)}*" + (f"\n{detalle}" if detalle else "") + sigue)

def resolver(clave, avisar=True):
    a = _mod_estado(lambda d: d["alertas"].pop(clave, None))
    if a and avisar:
        tg(f"✅ *{_nt('Solucionado')}*: {a.get('titulo', '')}\nVolvió a andar (estuvo {_dur(time.time() - a.get('desde', time.time()))} con problemas).")

# ══════════════════════════════════════════════════════════════════════════════
# TEXTO DE AYUDA
# ══════════════════════════════════════════════════════════════════════════════
AYUDA = r"""📋 *Comandos disponibles:*

/menu — Abre el panel de control con botones

*Estado y avisos*
/estado — Cómo está el bot: último ciclo, créditos y avisos pendientes
_Los avisos de noche (00 a 08 hs) llegan sin sonido. A las 9 hs llega el resumen del día._

*Sincronización*
/sync\_total — Sincroniza precios y stock de todos los productos
/ciclo — Dispara un ciclo de monitoreo manualmente
/reparar\_fotos — Sube a Cloudinary las fotos del proveedor que se ven rotas en la web
/pendientes — Productos nuevos del proveedor esperando aprobación (Publicar / Ignorar)
/aprobar\_nuevos on|off — Si está on, lo nuevo entra oculto hasta que lo apruebes
/registrar\_webhooks — Registra webhooks en Tienda Nube para notificaciones instantáneas
/ver\_webhooks — Muestra los webhooks activos
/borrar\_webhook ID — Elimina un webhook por ID

*Productos*
/listar — Lista los primeros 50 productos del catálogo
/precio Nombre 9999 — Cambia el precio de un producto
/stock Nombre 10 — Cambia el stock de un producto
/ocultar Nombre — Oculta un producto de la tienda
/publicar Nombre — Publica un producto oculto

*Envío gratis*
/fix\_envio\_gratis — Aplica envío gratis a todos los productos
/generar\_descripcion Nombre — Genera descripción con IA (Groq/Llama) para un producto
/generar\_todas\_descripciones — Genera descripciones para todos los productos sin descripción
/set\_video Nombre URL — Agrega video de YouTube a un producto
/generar\_todas\_descripciones — Genera descripciones para todos los productos sin descripción ≥ $100.000 (excepto mesas)
/productos\_sin\_cargar — Productos del proveedor que no tenés en tu tienda (filtrado)
/productos\_sin\_cargar todo — Ídem pero sin filtro (excluye solo Pantallas/Baterías)

*Ofertas*
/ver\_ofertas\_proveedor — Ver todas las ofertas activas del proveedor (numeradas)
/aplicar\_ofertas todos — Aplica todas las ofertas pendientes del proveedor
/aplicar\_ofertas 1 3 — Aplica las ofertas numeradas seleccionadas

*Debug*
/debug\_match Nombre — Verifica el matching de un producto con el proveedor
/debug\_producto ID — Muestra datos crudos de un producto por ID
/debug\_env — Muestra el estado de las variables de entorno

*API / Token*
/estado\_api — Muestra si el token está activo
/borrar\_token — Elimina el token guardado

*Pedidos*
/confirmar\_pedido NUMERO — Confirma y envía al proveedor un pedido con problemas
"""

# ══════════════════════════════════════════════════════════════════════════════
# OAUTH
# ══════════════════════════════════════════════════════════════════════════════
def canjear_code(code):
    try:
        r = requests.post(f"{API_BASE}/oauth/app/token",
            json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                  "grant_type": "authorization_code", "code": code},
            headers={"Content-Type":"application/json","User-Agent":USER_AGENT}, timeout=30)
        print(f"OAuth {r.status_code}: {r.text[:200]}")
        if r.status_code in (200, 201):
            d = r.json().get("data", r.json())
            t = d.get("access_token")
            u = str(d.get("store_id") or d.get("user_id") or "")
            if t and u: guardar_token(t, u); return t
    except Exception as e: print(f"❌ OAuth: {e}")
    return None

# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# API COMERCIAPP (web nueva) — destino de la sincronización
# ══════════════════════════════════════════════════════════════════════════════
def _ca_headers():
    return {"Content-Type": "application/json", "X-Bot-Key": COMERCIAPP_KEY}

def comerciapp_ok():
    return bool(COMERCIAPP_API and COMERCIAPP_KEY)

# Tamaño de lote para no timeout-ear el backend (463 de una vez > 60s). Configurable por env.
SYNC_CHUNK = int(os.environ.get("SYNC_CHUNK", "40") or "40")

def _comerciapp_sync_chunk(lote, ocultar_nuevos=False):
    """Manda UN sub-lote a ComerciApp (/api/bot/sync). Devuelve el JSON de respuesta o None."""
    payload = {"productos": lote, "ocultar_nuevos": bool(ocultar_nuevos)}
    if COMERCIAPP_SECCION:
        try: payload["seccion_id"] = int(COMERCIAPP_SECCION)
        except Exception: pass
    for intento in range(3):
        try:
            r = requests.post(f"{COMERCIAPP_API}/api/bot/sync", headers=_ca_headers(), json=payload, timeout=120)
            if r.status_code in (200, 201):
                return r.json()
            print(f"⚠️ ComerciApp sync HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code in (401, 503):  # auth/config error → no reintentar
                return None
        except Exception as e:
            print(f"❌ ComerciApp sync (intento {intento+1}/3): {e}")
        time.sleep(3 * (intento + 1))
    return None

def comerciapp_sync(lote, ocultar_nuevos=False):
    """Manda los productos a ComerciApp en sub-lotes chicos (evita timeout con lotes grandes).
    Agrega los contadores de cada sub-lote y devuelve un resumen combinado (o None si todo falló)."""
    if not comerciapp_ok():
        print("⚠️ ComerciApp no configurado (COMERCIAPP_API / BOT_API_KEY)")
        return None
    if not lote:
        return {"insertados":0,"actualizados":0,"errores":0,"total":0,"detalles":[]}

    total = {"insertados":0,"actualizados":0,"errores":0,"total":0,"detalles":[],"primer_error":"","nuevos":[],
             "cambios_precio":0,"cambios_stock":0,"cambios_costo":0,"sin_stock":[],"con_stock":[],"ok_skus":[],"lotes_fallidos":0}
    partes = [lote[i:i+SYNC_CHUNK] for i in range(0, len(lote), SYNC_CHUNK)]
    hubo_ok = False
    for idx, parte in enumerate(partes, 1):
        res = _comerciapp_sync_chunk(parte, ocultar_nuevos)
        if res is None:
            # Sub-lote fallido: contarlo como errores para no perder el rastro
            total["errores"] += len(parte)
            total["total"]   += len(parte)
            total["lotes_fallidos"] += 1
            print(f"   ⚠️ Sub-lote {idx}/{len(partes)} falló ({len(parte)} prods)")
            continue
        hubo_ok = True
        total["insertados"]   += res.get("insertados", 0)
        total["actualizados"] += res.get("actualizados", 0)
        total["errores"]      += res.get("errores", 0)
        total["total"]        += res.get("total", len(parte))
        for k in ("cambios_precio", "cambios_stock", "cambios_costo"):
            total[k] += res.get(k, 0) or 0
        total["sin_stock"].extend(res.get("sin_stock") or [])
        total["con_stock"].extend(res.get("con_stock") or [])
        # SKU que quedaron bien en la web (para no reenviarlos si no cambian)
        fallaron = {d.get("sku") for d in (res.get("detalles") or [])}
        if (res.get("errores", 0) or 0) <= len(fallaron):
            total["ok_skus"].extend(p["sku"] for p in parte if p.get("sku") not in fallaron)
        if res.get("detalles"):
            total["detalles"].extend(res["detalles"])
        if res.get("nuevos"):
            total["nuevos"].extend(res["nuevos"])
        if not total["primer_error"] and res.get("primer_error"):
            total["primer_error"] = res["primer_error"]
        print(f"   ✅ Sub-lote {idx}/{len(partes)}: +{res.get('insertados',0)} nuevos, {res.get('actualizados',0)} act.")
    return total if hubo_ok else None

def comerciapp_skus_existentes():
    """Devuelve dict {sku: stock} de los productos RXZ- que ya existen en ComerciApp (None si la web no respondió)."""
    if not comerciapp_ok(): return None
    try:
        r = requests.get(f"{COMERCIAPP_API}/api/bot/skus", headers=_ca_headers(), timeout=40)
        if r.status_code == 200:
            return {row["sku"]: row.get("stock", 0) for row in r.json().get("skus", [])}
        print(f"⚠️ ComerciApp skus HTTP {r.status_code}: {r.text[:150]}")
        comerciapp_skus_existentes.error = f"HTTP {r.status_code}"
    except Exception as e:
        print(f"❌ ComerciApp skus: {e}")
        comerciapp_skus_existentes.error = type(e).__name__
    return None

def salud_web():
    """Créditos de los servicios de la web (Cloudinary). None si no responde."""
    if not comerciapp_ok(): return None
    try:
        r = requests.get(f"{COMERCIAPP_API}/api/bot/salud", headers=_ca_headers(), timeout=30)
        if r.status_code == 200: return r.json()
    except Exception as e: print(f"❌ salud web: {e}")
    return None

_vendidos_cache = {"t": 0, "data": {}}
def vendidos_30d():
    """{sku: {nombre, unidades}} de los productos del proveedor vendidos en la web en 30 días (cache 1 h)."""
    if not comerciapp_ok(): return {}
    if time.time() - _vendidos_cache["t"] < 3600: return _vendidos_cache["data"]
    try:
        r = requests.get(f"{COMERCIAPP_API}/api/bot/vendidos", headers=_ca_headers(), params={"dias": 30}, timeout=30)
        if r.status_code == 200:
            _vendidos_cache["data"] = {p["sku"]: p for p in r.json().get("productos", []) if p.get("sku")}
            _vendidos_cache["t"] = time.time()
    except Exception as e: print(f"❌ vendidos: {e}")
    return _vendidos_cache["data"]

def enviar_latido():
    """Le cuenta a la web que el bot sigue vivo. Si deja de llegar, la web avisa por Telegram
    (el bot no puede avisar si está caído o si Railway lo frenó por falta de crédito)."""
    if not comerciapp_ok(): return
    try:
        est = leer_estado()
        datos = {"ultimo_ciclo": est.get("ultimo_ciclo", {}),
                 "alertas": [a.get("titulo") for a in est.get("alertas", {}).values()]}
        requests.post(f"{COMERCIAPP_API}/api/bot/latido", headers=_ca_headers(), timeout=20, json={
            "scraping_activo": bool(leer_db().get("scraping_activo", False)), "ciclo_min": LATIDO_MIN,
            "nombre": NOMBRE_TIENDA, "tg_token": TELEGRAM_TOKEN or "", "tg_chat": CHAT_ID or "", "datos": datos})
    except Exception as e: print(f"⚠️ latido: {e}")

def comerciapp_stock_cero(skus):
    """Pone stock 0 a una lista de SKU en ComerciApp (los que se cayeron del proveedor)."""
    if not comerciapp_ok() or not skus: return 0
    try:
        r = requests.post(f"{COMERCIAPP_API}/api/bot/stock-cero", headers=_ca_headers(), json={"skus": skus}, timeout=40)
        if r.status_code == 200:
            return r.json().get("afectados", 0)
    except Exception as e:
        print(f"❌ ComerciApp stock-cero: {e}")
    return 0

# ── Aprobación de productos NUEVOS del proveedor ──────────────────────────────
# Con /aprobar_nuevos on (por defecto), lo nuevo entra OCULTO en la web y el bot pregunta por
# Telegram si publicarlo. En la primera carga (web casi vacía) se publica directo.
APROBAR_MAX_FICHAS = 8   # hasta esta cantidad manda una ficha por producto; más, un resumen con "Publicar todos"

def _md(t):
    """Escapa texto para Markdown de Telegram (nombres con * _ ` [ rompen el mensaje)."""
    return re.sub(r"([_*`\[])", r"\\\1", str(t or ""))

def _lim(t):
    """Para texto DENTRO de *negrita*: Telegram no admite escapes ahí, así que se sacan esos caracteres."""
    return re.sub(r"[_*`\[\]]", "", str(t or "")).strip()

def tg_botones(texto, filas):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": texto, "parse_mode": "Markdown", "disable_notification": _es_silencio(),
                  "reply_markup": {"inline_keyboard": filas}}, timeout=15)
    except Exception as e: print(f"❌ Telegram botones: {e}")

def tg_foto_botones(url, caption, filas):
    """Ficha con foto + botones. Si Telegram no puede bajar la foto, manda solo el texto."""
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    if url and "cloudinary" in url:
        try:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                json={"chat_id": CHAT_ID, "photo": url, "caption": caption[:1000], "parse_mode": "Markdown",
                      "disable_notification": _es_silencio(), "reply_markup": {"inline_keyboard": filas}}, timeout=20)
            if r.status_code == 200: return
        except Exception: pass
    tg_botones(caption, filas)

def comerciapp_pendientes():
    if not comerciapp_ok(): return None
    try:
        r = requests.get(f"{COMERCIAPP_API}/api/bot/pendientes", headers=_ca_headers(), timeout=40)
        if r.status_code == 200: return r.json().get("productos", [])
        print(f"⚠️ pendientes HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e: print(f"❌ pendientes: {e}")
    return None

def comerciapp_aprobar(skus=None, todos=False, publicar=True):
    if not comerciapp_ok(): return None
    try:
        body = {"publicar": bool(publicar)}
        if todos: body["todos"] = True
        else: body["skus"] = list(skus or [])
        r = requests.post(f"{COMERCIAPP_API}/api/bot/aprobar", headers=_ca_headers(), json=body, timeout=40)
        if r.status_code == 200: return r.json()
        print(f"⚠️ aprobar HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e: print(f"❌ aprobar: {e}")
    return None

def _ficha_nuevo(p):
    precio = int(float(p.get("precio") or 0))
    cap = f"*{_lim(p.get('nombre'))}*\n$" + f"{precio:,}".replace(",", ".")
    if p.get("categoria"): cap += f"\n{_md(p.get('categoria'))}"
    cap += f"\n`{p.get('sku')}`"
    botones = [[{"text": "Publicar", "callback_data": f"/pub {p.get('sku')}"},
                {"text": "Ignorar", "callback_data": f"/ign {p.get('sku')}"}]]
    tg_foto_botones(p.get("imagen") or "", cap, botones)

def avisar_nuevos(nuevos):
    """Avisa los productos nuevos que quedaron ocultos esperando aprobación."""
    if not nuevos: return
    if len(nuevos) <= APROBAR_MAX_FICHAS:
        tg(f"🆕 *{_nt('Productos nuevos del proveedor')}*: {len(nuevos)}\nQuedaron *ocultos* en la web hasta que los apruebes.")
        for p in nuevos: _ficha_nuevo(p)
        return
    lista = "\n".join(f"• {_md(p.get('nombre'))}" for p in nuevos[:25])
    if len(nuevos) > 25: lista += f"\n_...y {len(nuevos) - 25} más_"
    tg_botones(f"🆕 *{_nt('Productos nuevos del proveedor')}*: {len(nuevos)}\nQuedaron *ocultos* en la web hasta que los apruebes.\n\n{lista}",
               [[{"text": f"Publicar todos ({len(nuevos)})", "callback_data": "/pub_todos"}],
                [{"text": "Revisar uno por uno", "callback_data": "/pendientes"},
                 {"text": "Ignorar todos", "callback_data": "/ign_todos"}]])

# ── Fotos del proveedor → Cloudinary (vía backend) ────────────────────────────
# rxzweb está detrás de Cloudflare: la web (Railway) y Cloudinary no pueden bajar esas
# fotos, por eso se ven rotas. El bot las baja con su proxy residencial y las manda a
# /api/bot/foto, que las sube a Cloudinary y reemplaza la URL en todos los productos.
FOTOS_MAX_BYTES     = 11 * 1024 * 1024
FOTOS_SEG_POR_CICLO = int(os.environ.get("FOTOS_SEG_POR_CICLO", "240") or "0")  # 0 = no reparar en cada ciclo
_fotos_lock = threading.Lock()

def comerciapp_fotos_externas():
    """{total, productos, urls:[...]} con las fotos de la web que siguen apuntando al proveedor."""
    if not comerciapp_ok(): return None
    try:
        r = requests.get(f"{COMERCIAPP_API}/api/bot/fotos-externas", headers=_ca_headers(), timeout=60)
        if r.status_code == 200:
            return r.json()
        print(f"⚠️ fotos-externas HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e:
        print(f"❌ fotos-externas: {e}")
    return None

def _es_imagen(r):
    ct = (r.headers.get("content-type") or "").lower()
    return r.status_code == 200 and ct.startswith("image/") and len(r.content or b"") > 500

def _bajar_foto(url, estado):
    """Baja una foto del proveedor. Primero directo (no gasta proxy); si Cloudflare bloquea,
    por el proxy residencial con una sesión caliente que se reutiliza (rota IP si da 403).
    Devuelve (bytes, content_type) o (None, motivo)."""
    if not CURL_CFFI_OK:
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
            return (r.content, r.headers.get("content-type")) if _es_imagen(r) else (None, f"HTTP {r.status_code}")
        except Exception as e:
            return None, type(e).__name__
    hdrs = {"Referer": "https://rxzweb.com/", "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    if not estado.get("directo_bloqueado"):
        try:
            r = cf_requests.get(url, impersonate="chrome124", headers=hdrs, timeout=25)
            if _es_imagen(r):
                return r.content, r.headers.get("content-type")
            if r.status_code == 404:
                return None, "404 en el proveedor"
            estado["directo_bloqueado"] = True
        except Exception:
            estado["directo_bloqueado"] = True
    if not _get_residential_proxy():
        return None, "bloqueado y sin proxy"
    motivo = "sin respuesta"
    for _ in range(3):
        s = estado.get("sess")
        if s is None:
            s, _p = _nueva_sesion_prov()
            if s is None:
                motivo = "no abre sesión proxy"; time.sleep(2); continue
            estado["sess"] = s
        try:
            r = s.get(url, headers=hdrs, timeout=40)
            if _es_imagen(r):
                return r.content, r.headers.get("content-type")
            if r.status_code == 404:
                return None, "404 en el proveedor"
            motivo = f"HTTP {r.status_code}"
        except Exception as e:
            motivo = type(e).__name__
        estado["sess"] = None   # IP quemada → la próxima abre otra
        time.sleep(1.5)
    return None, motivo

def _subir_foto(origen, data=None, ctype=None, nueva=None, remoto=False):
    """Sube la foto (o reemplaza por una URL de Cloudinary ya subida, o pide que Cloudinary la baje
    directo con remoto=True). Devuelve el JSON del backend."""
    try:
        if remoto:
            r = requests.post(f"{COMERCIAPP_API}/api/bot/foto", headers=_ca_headers(),
                              json={"origen": origen, "remoto": True}, timeout=90)
        elif nueva:
            r = requests.post(f"{COMERCIAPP_API}/api/bot/foto", headers=_ca_headers(),
                              json={"origen": origen, "nueva": nueva}, timeout=60)
        else:
            nombre = origen.split("?")[0].rsplit("/", 1)[-1] or "foto.jpg"
            r = requests.post(f"{COMERCIAPP_API}/api/bot/foto", headers={"X-Bot-Key": COMERCIAPP_KEY},
                              data={"origen": origen},
                              files={"imagen": (nombre, data, (ctype or "image/jpeg").split(";")[0])}, timeout=120)
        if r.status_code == 200:
            return r.json()
        return {"error": f"web HTTP {r.status_code}: {r.text[:100]}"}
    except Exception as e:
        return {"error": f"web {type(e).__name__}"}

def reparar_fotos(tiempo_max=None, avisar=False):
    """Pasa a Cloudinary las fotos de la web que todavía apuntan a rxzweb.
    tiempo_max: segundos máximos (None = todas). avisar: manda progreso por Telegram."""
    if not comerciapp_ok():
        if avisar: tg("❌ ComerciApp no configurado.")
        return None
    if not _fotos_lock.acquire(blocking=False):
        if avisar: tg("ℹ️ Ya hay una reparación de fotos en curso.")
        return None
    try:
        info = comerciapp_fotos_externas()
        if info is None:
            if avisar: tg("❌ No pude consultar las fotos de la web.")
            return None
        urls = info.get("urls") or []
        res = {"ok": 0, "fallidas": 0, "saltadas": 0, "pendientes": len(urls), "productos": info.get("productos", 0), "motivos": {}}
        if not urls:
            if avisar: tg("✅ No hay fotos del proveedor pendientes: todas están en Cloudinary.")
            return res
        db = leer_db()
        cache = db.get("fotos_cloudinary", {})   # origen → URL Cloudinary
        fallos = db.get("fotos_fallidas", {})    # origen → intentos fallidos
        if avisar:
            tg(f"📸 *Reparando fotos de la web*\n{len(urls)} imágenes en {info.get('productos', '?')} productos.\nTe aviso cada 100.")
        estado, inicio = {}, time.time()
        for i, u in enumerate(urls, 1):
            if tiempo_max and time.time() - inicio > tiempo_max:
                break
            if not avisar and fallos.get(u, 0) >= 3:   # en automático no insistir con las que fallan siempre
                res["saltadas"] += 1; continue
            r = _subir_foto(u, nueva=cache[u]) if u in cache else None
            if not r or r.get("error"):
                data, ct = _bajar_foto(u, estado)
                if data is None:
                    r = {"error": ct}
                    if ct != "404 en el proveedor":
                        r2 = _subir_foto(u, remoto=True)   # último intento: que Cloudinary la baje directo
                        if r2.get("url") and not r2.get("error"): r = r2
                elif len(data) > FOTOS_MAX_BYTES:
                    r = {"error": "foto de más de 11 MB"}
                else:
                    r = _subir_foto(u, data, ct)
            if r.get("url") and not r.get("error"):
                res["ok"] += 1; cache[u] = r["url"]; fallos.pop(u, None)
            else:
                res["fallidas"] += 1; fallos[u] = fallos.get(u, 0) + 1
                m = str(r.get("error") or "?")[:60]
                res["motivos"][m] = res["motivos"].get(m, 0) + 1
            if avisar and i % 100 == 0:
                tg(f"📸 Fotos: {i}/{len(urls)} — {res['ok']} subidas, {res['fallidas']} con error")
            time.sleep(0.2)
        res["pendientes"] = len(urls) - res["ok"]
        db = leer_db(); db["fotos_cloudinary"] = cache; db["fotos_fallidas"] = fallos; escribir_db(db)
        if avisar:
            msg = (f"✅ *Fotos reparadas*\n\n• Subidas a Cloudinary: {res['ok']}\n"
                   f"• Con error: {res['fallidas']}\n• Quedan del proveedor: {res['pendientes']}")
            if res["motivos"]:
                top = sorted(res["motivos"].items(), key=lambda x: -x[1])[:4]
                msg += "\n\nMotivos:\n" + "\n".join(f"• {k}: {v}" for k, v in top)
            tg(msg)
        return res
    except Exception as e:
        print(f"❌ reparar_fotos: {e}")
        if avisar: tg(f"❌ Error reparando fotos: `{e}`")
        return None
    finally:
        _fotos_lock.release()

# ══════════════════════════════════════════════════════════════════════════════
# API TIENDA NEGOCIO (LEGACY — ya no se usa, apuntamos a ComerciApp)
# ══════════════════════════════════════════════════════════════════════════════
def _h(): return {"Authorization":f"Bearer {_token}","User-Agent":USER_AGENT,"Content-Type":"application/json"}

def _get(url, params=None):
    for intento in range(4):
        try:
            r = requests.get(url, headers=_h(), params=params, timeout=40)
            if r.status_code == 429:
                espera = 5 * (intento + 1)
                print(f"   ⚠️ Rate limit GET. Esperando {espera}s..."); time.sleep(espera); continue
            return r
        except requests.exceptions.Timeout:
            print(f"   ⏱️ Timeout GET (intento {intento+1}/4): {url[:60]}")
            time.sleep(3 * (intento + 1))
        except Exception as e:
            print(f"❌ GET: {e}"); time.sleep(2)
    return None

def _put(url, data):
    for intento in range(4):
        try:
            r = requests.put(url, headers=_h(), json=data, timeout=40)
            if r.status_code == 429:
                espera = 15 * (intento + 1)
                print(f"   ⚠️ Rate limit PUT. Esperando {espera}s..."); time.sleep(espera); continue
            time.sleep(0.8)
            return r
        except requests.exceptions.Timeout:
            print(f"   ⏱️ Timeout PUT (intento {intento+1}/4): {url[:60]}")
            time.sleep(5 * (intento + 1))
        except Exception as e:
            print(f"❌ PUT: {e}"); time.sleep(2)
    return None

# ── Caché del catálogo ────────────────────────────────────────────────────────
_cat_cache = None
_cat_ts    = 0

def _fetch_variantes(pid):
    r_var = _get(f"{API_BASE}/products/{pid}/variants", params={"per_page":200})
    variantes = []
    if r_var and r_var.status_code == 200:
        dv = r_var.json()
        lista = dv.get("results", dv) if isinstance(dv,dict) else dv
        if isinstance(lista, list):
            for v in lista:
                vid = v.get("id")
                if not vid: continue
                vals = v.get("values", [])
                vnom = " ".join(str(x.get("es") or x.get("en","")).strip() for x in vals).strip()
                variantes.append({"id":vid,"nombre":vnom,"precio":float(v.get("price",0) or 0)})
    return variantes

def obtener_catalogo(forzar=False, pids_refrescar=None):
    global _cat_cache, _cat_ts
    if not forzar and _cat_cache and (time.time()-_cat_ts) < 300:
        return _cat_cache
    if not _token: return []

    if pids_refrescar is None:
        pids_refrescar = set()

    db = leer_db()
    var_cache = db.get("variantes_cache", {})

    print("📥 Descargando catálogo Tienda Negocio...")
    raw = []; pagina = 1
    while True:
        r = _get(f"{API_BASE}/products", params={"per_page":200,"page":pagina})
        if not r or r.status_code != 200:
            print(f"❌ Catálogo HTTP {r.status_code if r is not None else 'None'}"); break
        data = r.json()
        lote = data.get("results", data) if isinstance(data, dict) else data
        if not lote: break
        raw.extend(lote)
        print(f"   Pág {pagina}: {len(lote)} items (total: {len(raw)})")
        if not (data.get("pagination",{}).get("next_page") if isinstance(data,dict) else None): break
        pagina += 1; time.sleep(0.3)
    print(f"   {len(raw)} productos encontrados.")

    catalogo = []
    nuevas_en_cache = 0
    refrescadas = 0

    for p in raw:
        pid = p.get("id")
        nombre_raw = p.get("name", {})
        nombre = (nombre_raw.get("es") or nombre_raw.get("en") or
                  next(iter(nombre_raw.values()),"")) if isinstance(nombre_raw,dict) else str(nombre_raw or "")
        nombre = nombre.strip()
        if not nombre or not pid: continue

        pid_str = str(pid)
        es_nuevo       = pid_str not in var_cache
        debe_refrescar = pid in pids_refrescar

        if es_nuevo or debe_refrescar:
            variantes = _fetch_variantes(pid)
            var_cache[pid_str] = variantes
            time.sleep(0.4)
            if es_nuevo: nuevas_en_cache += 1
            else:        refrescadas += 1
        else:
            variantes = var_cache[pid_str]

        precio_base = variantes[0]["precio"] if variantes else float(p.get("price",0) or 0)
        catalogo.append({
            "id":pid, "nombre":nombre, "nombre_norm":normalizar(nombre),
            "precio_base":precio_base, "tiene_variantes":len(variantes)>0,
            "variantes":variantes, "published":p.get("published",True),
            "descripcion": ((lambda d: (d.get("es") or d.get("en") or next(iter(d.values()),"")) if isinstance(d, dict) else str(d or ""))(p.get("description",""))).strip()
        })

    pids_actuales = {str(p.get("id")) for p in raw if p.get("id")}
    eliminados = [k for k in var_cache if k not in pids_actuales]
    for k in eliminados: del var_cache[k]

    db["variantes_cache"] = var_cache
    escribir_db(db)

    info = f"   ✅ Catálogo: {len(catalogo)} productos"
    if nuevas_en_cache: info += f" | {nuevas_en_cache} nuevos cacheados"
    if refrescadas:     info += f" | {refrescadas} variantes refrescadas"
    if eliminados:      info += f" | {len(eliminados)} eliminados del cache"
    print(info)

    _cat_cache = catalogo; _cat_ts = time.time()
    return catalogo

# ── Operaciones API ───────────────────────────────────────────────────────────
def set_precio_variante(vid, precio):
    r = _put(f"{API_BASE}/variants/{vid}", {"price": str(int(precio))})
    ok = r and r.status_code in (200,201)
    if not ok: print(f"  ⚠️ precio variante {vid}: HTTP {r.status_code if r is not None else 'None'}")
    return ok

def set_precio_producto(pid, precio):
    r = _put(f"{API_BASE}/products/{pid}", {"price": str(int(precio))})
    ok = r and r.status_code in (200,201)
    if not ok: print(f"  ⚠️ precio producto {pid}: HTTP {r.status_code if r is not None else 'None'}")
    return ok

def set_stock_variante(vid, stock):
    r = _put(f"{API_BASE}/variants/{vid}", {"stock":int(stock),"stock_management":True})
    return r and r.status_code in (200,201)

def set_stock_producto(pid, stock):
    r = _put(f"{API_BASE}/products/{pid}", {"stock":int(stock),"stock_management":True})
    return r and r.status_code in (200,201)

def set_nombre_producto(pid, nombre):
    r = _put(f"{API_BASE}/products/{pid}", {"name":{"es":nombre}})
    return r and r.status_code in (200,201)

def set_visibilidad(pid, published):
    r = _put(f"{API_BASE}/products/{pid}", {"published":published})
    return r and r.status_code in (200,201)

def set_envio_gratis(pid, activo):
    r = _put(f"{API_BASE}/products/{pid}", {"freeshipping": activo})
    ok = r and r.status_code in (200,201)
    if not ok: print("  Envio gratis HTTP " + str(r.status_code if r is not None else "None"))
    return ok

# ══════════════════════════════════════════════════════════════════════════════
# ENVÍO GRATIS — LÓGICA CENTRALIZADA
# ══════════════════════════════════════════════════════════════════════════════
def _debe_tener_envio_gratis(pid, precio):
    if pid in PRODUCTOS_PESADOS_IDS:
        return False
    return precio >= ENVIO_GRATIS_MIN

def _actualizar_envio_gratis_prod(prod, precio):
    pid = prod["id"]
    if prod.get("variantes"):
        precio_max = max(v["precio"] for v in prod["variantes"])
    else:
        precio_max = precio
    activo = _debe_tener_envio_gratis(pid, precio_max)
    set_envio_gratis(pid, activo)

def run_fix_envio_gratis():
    if not _token:
        tg("❌ Necesito el token primero."); return
    tg(f"🚚 *{_nt('Fix envío gratis iniciado')}*\nRecorriendo catálogo...")
    catalogo = obtener_catalogo(forzar=True)
    if not catalogo:
        tg("❌ No pude obtener el catálogo."); return
    db   = leer_db()
    prov = db.get("productos_proveedor", {})
    sinc = db.get("sincronizados", {})
    if not prov:
        tg("\u26a0\ufe0f Sin datos del proveedor. Esperá un ciclo primero."); return
    idx = construir_indice(prov)
    activados    = []
    desactivados = []
    pesados_skip = []
    for prod in catalogo:
        pid    = prod["id"]
        nombre = prod["nombre"]
        if pid in PRODUCTOS_PESADOS_IDS:
            pesados_skip.append(f"• *{nombre}* (pesado, sin tocar)")
            continue
        _, datos_prov = buscar_en_indice(prod["nombre_norm"], idx)
        if datos_prov:
            if datos_prov.get("variantes"):
                precio_max = max(precio_obj(vd["precio"]) for vd in datos_prov["variantes"].values())
            else:
                precio_max = precio_obj(datos_prov["precio_base"])
        else:
            precio_max = sinc.get(nombre, {}).get("precio", 0)
        if precio_max >= ENVIO_GRATIS_MIN:
            if set_envio_gratis(pid, True):
                activados.append(f"• *{nombre}* — ${int(precio_max):,}")
        else:
            if set_envio_gratis(pid, False):
                desactivados.append(f"• *{nombre}* — ${int(precio_max):,}")
        time.sleep(0.5)
    resumen = (f"✅ *{_nt('Fix envío gratis terminado')}*\n\n"
               f"🚚 Activados: *{len(activados)}*\n"
               f"❌ Desactivados: *{len(desactivados)}*\n"
               f"⏭️ Pesados (sin tocar): *{len(pesados_skip)}*")
    tg(resumen)
    if activados:
        for i in range(0, len(activados), 30):
            tg("🚚 *Con envío gratis:*\n\n" + "\n".join(activados[i:i+30]))
    if pesados_skip:
        tg("⏭️ *Productos pesados (cartel manual en foto):*\n\n" + "\n".join(pesados_skip))

# ══════════════════════════════════════════════════════════════════════════════
# SCRAPING PROVEEDOR — API WOOCOMMERCE PÚBLICA
# ══════════════════════════════════════════════════════════════════════════════
SCRAPERAPI_KEY  = _e("SCRAPERAPI_KEY")
SCRAPERAPI_KEY2 = _e("SCRAPERAPI_KEY2")
SCRAPERAPI_URL = "http://api.scraperapi.com"

# Diagnóstico de cada ciclo: qué servicio falló y si parece falta de crédito (para avisar por Telegram).
_diag = {}
_RE_CREDITO = re.compile(r"balance|insufficient|credit|quota|traffic|saldo|余额|payment|expired|suspend", re.I)

def _diag_falla(servicio, codigo=None, texto=""):
    d = _diag.setdefault(servicio, {"fallas": 0})
    d["fallas"] += 1
    d["ultimo"] = ((f"HTTP {codigo}: " if codigo else "") + re.sub(r"\s+", " ", str(texto or "")))[:160].replace("`", "")
    if codigo in (401, 402, 407) or _RE_CREDITO.search(f"{codigo or ''} {texto or ''}"):
        d["credito"] = True

def _diag_ok(servicio):
    _diag.setdefault(servicio, {"fallas": 0})["ok"] = True
    _diag["fuente"] = servicio
    _diag[f"n_{servicio}"] = _diag.get(f"n_{servicio}", 0) + 1   # pedidos que salieron bien (los que se cobran)

def creditos_scraperapi():
    """Uso de cada clave de ScraperAPI (es el último respaldo para leer el proveedor)."""
    out = []
    for i, k in enumerate([SCRAPERAPI_KEY, SCRAPERAPI_KEY2], 1):
        if not k: continue
        try:
            r = requests.get(f"{SCRAPERAPI_URL}/account", params={"api_key": k}, timeout=20)
            if r.status_code == 200:
                j = r.json(); usado = int(j.get("requestCount") or 0); lim = int(j.get("requestLimit") or 0)
                out.append({"key": i, "usado": usado, "limite": lim, "pct": round(usado * 100 / lim) if lim else None})
            else:
                out.append({"key": i, "error": f"HTTP {r.status_code}"})
        except Exception as e:
            out.append({"key": i, "error": type(e).__name__})
    return out

def _via_scraperapi(url, params=None):
    keys = [k for k in [SCRAPERAPI_KEY, SCRAPERAPI_KEY2] if k]
    if not keys:
        print("⚠️ Sin SCRAPERAPI_KEY configurada — no puedo usar fallback")
        return None
    target = url
    if params:
        target += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    for i, key in enumerate(keys, 1):
        try:
            r = requests.get(SCRAPERAPI_URL, params={
                "api_key": key,
                "url": target,
            }, timeout=60)
            if r.status_code == 403 or (r.status_code == 200 and not r.text.strip()):
                print(f"⚠️ ScraperAPI key {i} sin créditos o bloqueada, probando siguiente...")
                _diag_falla("scraperapi", 403 if r.status_code == 403 else None, f"clave {i}: sin créditos o bloqueada " + (r.text or "")[:80])
                if r.status_code == 403: _diag["scraperapi"]["credito"] = True
                continue
            print(f"✅ ScraperAPI key {i} respondió HTTP {r.status_code}")
            if r.status_code == 200: _diag_ok("scraperapi")
            return r
        except Exception as e:
            print(f"⚠️ ScraperAPI key {i}: {e}")
            _diag_falla("scraperapi", None, f"clave {i}: {type(e).__name__}")
            continue
    print("❌ Todas las keys de ScraperAPI fallaron")
    return None

# ── ThorData Web Unlocker / Universal Scraping API (resuelve el challenge JS de Cloudflare) ──
THORDATA_TOKEN = _e("THORDATA_TOKEN")
THORDATA_URL   = "https://webunlocker.thordata.com/request"

class _ThorResp:
    """Respuesta minima compatible con requests.Response (status_code/.text/.json())."""
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text or ""
    def json(self):
        import json as _json
        return _json.loads((self.text or "").replace("\x00", ""), strict=False)

def _extraer_json(texto):
    """Devuelve el JSON crudo (str) de productos a partir de la respuesta de ThorData,
    que puede venir: (a) como array/objeto de productos directo, (b) envuelta en un
    JSON sobre tipo {\"code\":..,\"data\":\"...\"}, o (c) dentro de HTML renderizado."""
    if not texto:
        return None
    import json as _json, re, html as _html
    t = texto.strip()

    # (a) Array de productos directo
    if t[:1] == "[":
        return t

    # (a/b) Objeto JSON: puede ser un producto suelto o un SOBRE de ThorData
    if t[:1] == "{":
        try:
            obj = _json.loads(t.replace("\x00", ""), strict=False)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            # Producto suelto de WooCommerce (endpoint /products/{id})
            if "id" in obj and ("name" in obj or "prices" in obj):
                return t
            # Sobre con código de error (ej. {"code":401,"data":"User balance is insufficient!"}): no son datos
            if "code" in obj and str(obj.get("code")) not in ("0", "200", "None"):
                return None
            # Sobre de ThorData: el contenido real esta en algun campo
            for campo in ("data", "body", "html", "content", "result", "response", "text", "page"):
                v = obj.get(campo)
                if isinstance(v, str) and v.strip():
                    inner = _extraer_json(v)     # recursivo: el string interno puede ser JSON o HTML
                    if inner:
                        return inner
                elif isinstance(v, (list, dict)):
                    return _json.dumps(v)
            return None
        return t

    # (c) HTML: visor JSON del navegador (<pre>) o primer array/objeto embebido
    m = re.search(r"<pre[^>]*>(.*?)</pre>", texto, re.S | re.I)
    if m:
        cand = _html.unescape(m.group(1)).strip()
        inner = _extraer_json(cand)
        if inner:
            return inner
    m = re.search(r"(\[.*\]|\{.*\})", texto, re.S)
    if m:
        return m.group(1).strip()
    return None

def _via_thordata(url, params=None):
    if not THORDATA_TOKEN:
        return None
    if _diag.get("thordata", {}).get("credito"):
        return None   # ya avisó que no tiene saldo en este ciclo: no gastar tiempo reintentando
    from urllib.parse import urlencode as _ue
    target = url
    if params:
        target += ("&" if "?" in target else "?") + _ue(params)
    headers = {
        "Authorization": f"Bearer {THORDATA_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    # Web Unlocker: renderiza JS y resuelve Cloudflare automaticamente (type=html => cuerpo crudo)
    for intento in range(2):
        try:
            r = requests.post(THORDATA_URL, headers=headers,
                              data={"url": target, "type": "html"}, timeout=90)
        except Exception as e:
            print(f"⚠️ ThorData (intento {intento+1}/2): {e}")
            _diag_falla("thordata", None, type(e).__name__)
            time.sleep(3); continue
        if r.status_code != 200:
            print(f"⚠️ ThorData HTTP {r.status_code}: {(r.text or '')[:120]}")
            _diag_falla("thordata", r.status_code, (r.text or "")[:200])
            if r.status_code in (401, 402): break   # sin crédito o token inválido: no insistir
            time.sleep(2); continue
        crudo = _extraer_json(r.text)
        if crudo:
            print("✅ ThorData OK")
            _diag_ok("thordata")
            return _ThorResp(200, crudo)
        print(f"⚠️ ThorData sin JSON util: {(r.text or '')[:120]}")
        # Algunos errores de saldo vienen con HTTP 200 y un JSON de error
        _diag_falla("thordata", None, "respuesta sin datos: " + (r.text or "")[:150])
        if _diag["thordata"].get("credito"): break
        time.sleep(2)
    return None

# Sesion curl_cffi caliente reutilizable (IP sticky + cookie cf_clearance de Cloudflare)
_prov_cf_sess = None

def _nueva_sesion_prov():
    """Abre una sesion curl_cffi con IP sticky de ThorData y calienta la home
    para que Cloudflare entregue la cookie cf_clearance (queda guardada en la
    sesion). Asi el request al /wp-json va con la MISMA IP + cookie y no da 403.
    Devuelve (session, proxy_url) o (None, None)."""
    import random as _rnd
    if not CURL_CFFI_OK:
        return None, None
    proxy = _get_residential_proxy(sess=_rnd.randint(100000, 999999))
    try:
        if proxy:
            s = cf_requests.Session(impersonate="chrome124",
                                    proxies={"http": proxy, "https": proxy})
        else:
            s = cf_requests.Session(impersonate="chrome124")
        s.get("https://rxzweb.com/", timeout=20)   # calienta -> guarda cf_clearance
        time.sleep(1)
        return s, proxy
    except Exception as e:
        print(f"   ⚠️ No se pudo abrir sesion proveedor: {e}")
        if proxy: _diag_falla("proxy", 407 if "407" in str(e) else (402 if "402" in str(e) else None), str(e))
        return None, None

def _prov_get(url, params=None, usar_scraperapi=False):
    if usar_scraperapi:
        return _via_scraperapi(url, params)

    # ThorData Web Unlocker: metodo principal (unico que pasa el challenge JS de Cloudflare)
    if THORDATA_TOKEN:
        r = _via_thordata(url, params)
        if r is not None:
            return r
        print("⚠️ ThorData no devolvio datos — probando proxy residencial...")

    global _prov_cf_sess
    MAX_INTENTOS = 5

    # Sin curl_cffi: request plano (sin bypass Cloudflare)
    if not CURL_CFFI_OK:
        try:
            return requests.get(url, params=params, timeout=20,
                                headers={"User-Agent": USER_AGENT})
        except Exception as e:
            print(f"⚠️ Proveedor API: {e}")
            return None

    for intento in range(MAX_INTENTOS):
        # Reusar sesion caliente; si no hay, abrir una nueva (IP sticky + calentar)
        if _prov_cf_sess is None:
            _prov_cf_sess, _ = _nueva_sesion_prov()
            if _prov_cf_sess is None:
                time.sleep(3); continue
        try:
            r = _prov_cf_sess.get(url, params=params, timeout=45)
            if r.status_code == 429:
                print("⚠️ Rate limit proveedor"); time.sleep(3); continue
            if r.status_code == 403 and intento < MAX_INTENTOS - 1:
                print(f"⚠️ Cloudflare 403 (intento {intento+1}/{MAX_INTENTOS}) — rotando IP + recalentando...")
                _diag_falla("proxy", None, "Cloudflare 403")
                _prov_cf_sess = None   # descartar IP quemada; la proxima abre otra
                time.sleep(2); continue
            if r.status_code in (200, 201): _diag_ok("proxy")
            else: _diag_falla("proxy", r.status_code, "respuesta del proveedor")
            return r
        except Exception as e:
            print(f"⚠️ Proveedor API (intento {intento+1}/{MAX_INTENTOS}): {e}")
            _diag_falla("proxy", 407 if "407" in str(e) else (402 if "402" in str(e) else None), str(e))
            _prov_cf_sess = None
            time.sleep(3)
    return None

def _precio_real(p):
    return int(p.get("prices",{}).get("price", 0)) // 100

def _stock_real(p):
    return p.get("add_to_cart",{}).get("maximum") or 0

def _categoria_excluida(cat_nombre, excluidas):
    """True si la categoría está en la lista de excluidas (parcial, minúsculas, sin acentos)."""
    if not cat_nombre: return False
    def _sin_acentos(s):
        return (s.lower().strip()
                .replace('á','a').replace('é','e').replace('í','i')
                .replace('ó','o').replace('ú','u'))
    c = _sin_acentos(cat_nombre)
    return any(_sin_acentos(ex) in c for ex in excluidas)

PRODUCTOS_CON_VARIANTES = {'jc face id flex tag', 'jc bateria flex tag', 'maneral mango mijing'}
PROV_COMPLETA_CADA_H = 24   # una vez por día se lee el catálogo entero (incluye categorías excluidas) para recalcular el filtro

def _firma_excluidas(excluidas):
    return "|".join(sorted(str(x).lower() for x in (excluidas or [])))

def _entrada_simple(p, nombre_orig, nombre_base, cat_nombre):
    """Arma la entrada de un producto simple (mismo formato de siempre)."""
    precio_pub      = _precio_real(p)
    precio_original = int(p.get("prices", {}).get("regular_price", 0)) // 100
    en_oferta       = p.get("on_sale", False) and 0 < precio_pub < precio_original
    lista_imgs = []
    for im in p.get("images", []) or []:
        u = im.get("src") or im.get("thumbnail") or ""
        if u and u not in lista_imgs:
            lista_imgs.append(u)
    desc_raw = p.get("description", "") or p.get("short_description", "") or ""
    desc = re.sub(r"<[^>]+>", "", desc_raw).strip()[:2000]
    def _f(x):
        try: return float(x or 0)
        except Exception: return 0.0
    dims = p.get("dimensions", {}) or {}
    return {
        "nombre_real":           nombre_orig,
        "nombre_base_proveedor": nombre_base,
        "precio":                precio_pub,
        "precio_anterior":       precio_original if en_oferta else 0,
        "en_oferta":             en_oferta,
        "stock":                 (_stock_real(p) if p.get("is_in_stock", False) else 0),
        "woo_id":                p.get("id"),
        "imagen":                lista_imgs[0] if lista_imgs else "",
        "imagenes":              lista_imgs,
        "categoria":             cat_nombre,
        "descripcion":           desc,
        "peso":                  _f(p.get("weight")),
        "alto":                  _f(dims.get("height")),
        "ancho":                 _f(dims.get("width")),
        "largo":                 _f(dims.get("length")),
    }

def _entrada_variante(vd, nombre_orig, nombre_base):
    """Arma la entrada de una variante (mismo formato de siempre). None si no tiene precio."""
    v_var_str  = vd.get("variation", "") or ""
    v_nombre   = v_var_str.split(":", 1)[1].strip() if ":" in v_var_str else v_var_str
    v_precio   = _precio_real(vd)
    v_original = int(vd.get("prices", {}).get("regular_price", 0)) // 100
    v_oferta   = vd.get("on_sale", False) and 0 < v_precio < v_original
    if v_precio == 0: return None, None
    clave = normalizar(f"{nombre_orig} ({v_nombre})")
    return clave, {
        "nombre_real":           f"{nombre_orig} ({v_nombre})",
        "nombre_base_proveedor": nombre_base,
        "precio":                v_precio,
        "precio_anterior":       v_original if v_oferta else 0,
        "en_oferta":             v_oferta,
        "stock":                 (_stock_real(vd) if vd.get("is_in_stock", False) else 0),
        "woo_id":                vd.get("id"),
    }

def _pedir_lista(params, via_scraperapi):
    """Un pedido al proveedor que devuelve una lista de productos. None si falló."""
    r = _prov_get(PROV_API, params=params, usar_scraperapi=via_scraperapi)
    if not r or r.status_code != 200 or not (r.text or "").strip():
        return None
    try:
        lista = r.json()
    except Exception:
        return None
    return lista if isinstance(lista, list) else None

def _leer_variantes(grupos, via_scraperapi, info):
    """Variantes de los productos variables conocidos. Antes: un pedido por variante (~50 por ciclo).
    Ahora: todas juntas en uno. Si el proveedor no lo acepta o falta alguna, se piden una por una como antes."""
    padre = {vid: (no, nb) for no, nb, vids in grupos for vid in vids}
    ids = list(padre)
    got = {}
    if ids and info.get("variantes_lote", True):
        for i in range(0, len(ids), 100):
            parte = ids[i:i + 100]
            lista = _pedir_lista({"type": "variation", "include": ",".join(map(str, parte)), "per_page": 100}, via_scraperapi)
            for vd in lista or []:
                if isinstance(vd, dict) and vd.get("id") in padre:
                    got[vd["id"]] = vd
        if not got:
            info["variantes_lote"] = False   # el proveedor no lo acepta: desde ahora, una por una
            print("   ⚠️ Variantes en lote: el proveedor no lo acepta — se piden una por una")
        else:
            info["variantes_lote"] = True
    faltan = [vid for vid in ids if vid not in got]
    if faltan and got:
        print(f"   ⚠️ Variantes en lote: faltaron {len(faltan)}, se piden una por una")
    ok_todas = True
    for vid in faltan:
        rv = _prov_get(f"{PROV_API}/{vid}", usar_scraperapi=via_scraperapi)
        try:
            vd = rv.json() if rv is not None and rv.status_code == 200 and (rv.text or "").strip() else None
        except Exception:
            vd = None
        if isinstance(vd, dict): got[vid] = vd
        else: ok_todas = False
        time.sleep(0.25)
    out = {}
    for vid, vd in got.items():
        no, nb = padre[vid]
        clave, entrada = _entrada_variante(vd, no, nb)
        if clave: out[clave] = entrada
    return out, ok_todas

def scrapear_proveedor(excluidas=None, completa=None):
    """Lee el catálogo del proveedor (API WooCommerce). Para gastar menos pedidos del Web Unlocker:
    - liviana (normal): le pide al proveedor que NO mande las categorías excluidas (módulos, baterías);
    - completa (1 vez por día, o si cambió la lista de excluidas): trae todo y recalcula ese filtro.
    Una lectura liviana que trae menos productos de lo esperado se rehace completa en el momento."""
    db = leer_db()
    if excluidas is None:
        excluidas = db.get("categorias_excluidas", CATEGORIAS_EXCLUIDAS_DEFAULT)
    info = dict(db.get("lectura_prov") or {})
    if completa:
        info.pop("variantes_lote", None)   # en la lectura completa se vuelve a probar pedir las variantes juntas
    if completa is None:
        completa = not (info.get("excl_ids") and info.get("firma") == _firma_excluidas(excluidas)
                        and time.time() - info.get("completa_at", 0) < PROV_COMPLETA_CADA_H * 3600)
    _diag.clear()
    scrapear_proveedor.completo = False
    global _prov_cf_sess
    _prov_cf_sess = None
    print(f"📥 API proveedor ({'completa' if completa else 'liviana: sin categorías excluidas'})...")

    productos = {}; pagina = 1; reintentos_202 = 0; via_scraperapi = False; vacias = 0; reintentos_pag = 0
    categorias_vistas = set()
    completo = False
    excl_ids, cat_ids_de = set(), {}   # para recalcular el filtro en la lectura completa
    grupos_var = []                    # (nombre, nombre_base, [ids de variantes])
    n_padres = 0                       # productos (no variantes) que pasaron el filtro
    vistos = set()
    extra_ids = [int(x) for x in info.get("extra_ids", [])] if not completa else []

    def _procesar(lote):
        nonlocal n_padres
        for p in lote:
            if not isinstance(p, dict) or p.get("id") in vistos: continue
            vistos.add(p.get("id"))
            nombre_orig = (p.get("name") or "").strip()
            if not nombre_orig or len(nombre_orig) < 4: continue
            nombre_base = normalizar(nombre_orig)
            if _precio_real(p) == 0: continue
            cats = p.get("categories", []) or []
            cat_nombre = (cats[0].get("name") or "") if cats else ""
            if cat_nombre: categorias_vistas.add(cat_nombre)
            if _categoria_excluida(cat_nombre, excluidas):
                if cats and cats[0].get("id"): excl_ids.add(cats[0]["id"])
                continue
            n_padres += 1
            cat_ids_de[p.get("id")] = [c.get("id") for c in cats if c.get("id")]
            if p.get("type", "simple") == "variable" and any(pv in nombre_base for pv in PRODUCTOS_CON_VARIANTES):
                vids = [v.get("id") for v in p.get("variations", []) or [] if v.get("id")]
                if vids: grupos_var.append((nombre_orig, nombre_base, vids))
            else:
                productos[nombre_base] = _entrada_simple(p, nombre_orig, nombre_base, cat_nombre)

    while True:
        params = {"per_page": 100, "page": pagina}
        if not completa:
            params.update({"category": ",".join(map(str, info["excl_ids"])), "category_operator": "not_in"})
        r = _prov_get(PROV_API, params=params, usar_scraperapi=via_scraperapi)
        if not r or r.status_code not in (200, 201, 202):
            codigo = r.status_code if r is not None else 'None'
            print(f"❌ Proveedor HTTP {codigo}")
            if not via_scraperapi and reintentos_pag < 2:
                reintentos_pag += 1
                print(f"🔁 Reintentando la página {pagina} ({reintentos_pag}/2) en {10 * reintentos_pag}s...")
                _prov_cf_sess = None
                time.sleep(10 * reintentos_pag)
                continue
            scraper_ok = (SCRAPERAPI_KEY or SCRAPERAPI_KEY2) and not _diag.get("scraperapi", {}).get("credito")
            if codigo == 403 and not via_scraperapi and scraper_ok:
                print("🔄 HTTP 403 — activando ScraperAPI como fallback...")
                via_scraperapi = True
                continue
            break
        if r.status_code == 202:
            reintentos_202 += 1
            if reintentos_202 == 2 and not via_scraperapi and (SCRAPERAPI_KEY or SCRAPERAPI_KEY2) and not _diag.get("scraperapi", {}).get("credito"):
                print("🔄 Activando ScraperAPI como fallback...")
                via_scraperapi = True
                continue
            if reintentos_202 >= 5:
                print(f"❌ Proveedor HTTP 202 x{reintentos_202} - abortando scrape")
                _diag_falla("proveedor", 202, "no responde (HTTP 202 x5)")
                break
            espera = 30 if reintentos_202 <= 2 else 60
            print(f"⚠️ Proveedor HTTP 202 ({reintentos_202}/5) - reintentando en {espera}s...")
            time.sleep(espera)
            continue
        if not r.text or not r.text.strip():
            vacias += 1
            if vacias >= 3:
                print(f"❌ Proveedor: {vacias} respuestas vacías en pág {pagina} — se corta (lectura incompleta)")
                _diag_falla("proveedor", None, f"respuestas vacías en la página {pagina}")
                break
            print(f"⚠️ Proveedor respuesta vacía en pág {pagina}, reintentando...")
            time.sleep(5)
            continue
        try:
            lote = r.json()
        except Exception as e:
            print(f"⚠️ Proveedor JSON inválido pág {pagina}: {e}")
            _diag_falla("proveedor", None, f"respuesta inválida en la página {pagina}")
            break
        if not isinstance(lote, list):
            print(f"⚠️ Proveedor: respuesta inesperada en pág {pagina} — se corta (lectura incompleta)")
            _diag_falla("proveedor", None, f"respuesta inesperada en la página {pagina}: " + str(lote)[:100])
            break
        if not lote:
            completo = True; break
        _procesar(lote)
        reintentos_pag = 0
        print(f"   Pág {pagina}: {len(lote)} prods (total entradas: {len(productos)})")
        if len(lote) < 100:
            completo = True; break
        pagina += 1; time.sleep(0.5)

    # Liviana: los productos que tienen ALGUNA categoría excluida de segunda (no de primera) no vienen con el filtro: se piden aparte
    if completo and not completa and extra_ids:
        for i in range(0, len(extra_ids), 100):
            lista = _pedir_lista({"include": ",".join(map(str, extra_ids[i:i + 100])), "per_page": 100}, via_scraperapi)
            if lista is None:
                completo = False; break
            _procesar(lista)

    # Liviana que trae bastante menos de lo esperado → no confiar: se rehace completa ahora mismo
    if completo and not completa and n_padres < 0.97 * (info.get("n_padres") or 0):
        print(f"   ⚠️ Lectura liviana trajo {n_padres} productos (se esperaban ~{info.get('n_padres')}): se rehace completa")
        n_prev = _diag.get("n_thordata", 0)
        res = scrapear_proveedor(excluidas, completa=True)
        _diag["n_thordata"] = _diag.get("n_thordata", 0) + n_prev   # que el contador incluya la lectura descartada
        return res

    # Variantes (todas juntas en un pedido)
    if grupos_var:
        vars_, ok_vars = _leer_variantes(grupos_var, via_scraperapi, info)
        productos.update(vars_)
        if not ok_vars and completo:
            print("   ⚠️ Faltaron variantes: se toma como lectura incompleta (no se marca nada sin stock por faltante)")
            completo = False

    # Guardar el filtro para las próximas lecturas livianas
    if completo and completa:
        excl = sorted(excl_ids)
        info.update({"excl_ids": excl, "firma": _firma_excluidas(excluidas), "completa_at": time.time(), "n_padres": n_padres,
                     "extra_ids": sorted(pid for pid, cids in cat_ids_de.items() if any(c in excl_ids for c in cids))})
    db2 = leer_db(); db2["lectura_prov"] = info; escribir_db(db2)

    print(f"   ✅ {len(productos)} entradas del proveedor{'' if completo else ' (lectura INCOMPLETA)'}"
          f" · {_diag.get('n_thordata', 0)} pedidos al Web Unlocker")
    scrapear_proveedor.ultimas_categorias = sorted(categorias_vistas)
    scrapear_proveedor.completo = completo
    scrapear_proveedor.modo = "completa" if completa else "liviana"
    return productos

# ══════════════════════════════════════════════════════════════════════════════
# MATCHING DE NOMBRES
# ══════════════════════════════════════════════════════════════════════════════
STOP = {'de','para','con','el','la','los','las','un','una','y','en','del','al','a','o','e'}

def normalizar(s):
    return " ".join(re.sub(r'[^a-z0-9 ]',' ', str(s).lower()).split())

def palabras(n):
    return set(n.split()) - STOP

def match(n1, n2):
    if n1 == n2: return True
    w1 = palabras(n1); w2 = palabras(n2)
    if not w1 or not w2: return False
    criticas = {'bateria','battery','bat','face','maneral','mango','zocalo',
                'board','mini','plus','max','kit','ultra','xl',
                'lente','microscopio','trinocular','fuente','estacion','silla','cabina',
                'cam','4k','camara','wifi','pro','lite','set'}
    for c in criticas:
        if (c in w1) != (c in w2): return False
    n1s = {w for w in w1 if any(c.isdigit() for c in w)}
    n2s = {w for w in w2 if any(c.isdigit() for c in w)}
    if n1s and n2s:
        corto = n1s if len(w1) <= len(w2) else n2s
        largo = n2s if len(w1) <= len(w2) else n1s
        if not corto.issubset(largo): return False
    return len(w1 & w2) / min(len(w1), len(w2)) >= 0.75

# ══════════════════════════════════════════════════════════════════════════════
# PRECIO OBJETIVO
# ══════════════════════════════════════════════════════════════════════════════
def precio_obj(costo):
    raw = costo / MARGEN
    if raw >= 100000: return round(raw/1000)*1000
    if raw >= 10000:  return round(raw/500)*500
    if raw >= 1000:   return round(raw/100)*100
    return round(raw/50)*50

# ══════════════════════════════════════════════════════════════════════════════
# ÍNDICE DEL PROVEEDOR
# ══════════════════════════════════════════════════════════════════════════════
def construir_indice(prov):
    idx = {}
    for clave, d in prov.items():
        base = normalizar(d.get("nombre_base_proveedor", clave))
        if base not in idx:
            idx[base] = {"precio_base":d["precio"], "precio_anterior":d.get("precio_anterior",0),
                         "stock_base":d.get("stock",0),
                         "en_oferta":d.get("en_oferta",False), "variantes":{}}
        nombre_real = d.get("nombre_real", clave)
        if '(' in nombre_real:
            var_part = nombre_real.split('(',1)[-1].rstrip(')')
            var_norm = normalizar(var_part)
            if var_norm:
                idx[base]["variantes"][var_norm] = {
                    "precio":          d["precio"],
                    "stock":           d.get("stock",0),
                    "en_oferta":       d.get("en_oferta",False),
                    "precio_anterior": d.get("precio_anterior",0),
                }
        else:
            if d["precio"] < idx[base]["precio_base"]:
                idx[base]["precio_base"]    = d["precio"]
                idx[base]["en_oferta"]      = d.get("en_oferta",False)
                idx[base]["precio_anterior"]= d.get("precio_anterior",0)
            if d.get("stock",0) > idx[base]["stock_base"]:
                idx[base]["stock_base"] = d.get("stock",0)
    return idx

def buscar_en_indice(nombre_norm, idx):
    for base, datos in idx.items():
        if match(nombre_norm, base): return base, datos
    return None, None

# ══════════════════════════════════════════════════════════════════════════════
# SINCRONIZACIÓN DE PRECIOS
# ══════════════════════════════════════════════════════════════════════════════
def sincronizar_precios(prod, datos_prov):
    pid            = prod["id"]
    variantes      = prod["variantes"]
    en_oferta      = datos_prov.get("en_oferta", False)
    precio_anterior = datos_prov.get("precio_anterior", 0)

    if not variantes:
        p = precio_obj(datos_prov["precio_base"])
        precio_actual = prod.get("precio_base", 0)
        if precio_actual > 0 and p > 0 and (p / precio_actual) < 0.40:
            print("BLOQUEADO baja >60%: " + prod.get("nombre","?")[:40])
            return False, 0, 0
        if en_oferta and precio_anterior > 0:
            p_orig = precio_obj(precio_anterior)
            r = _put(f"{API_BASE}/products/{pid}", {
                "price": str(int(p_orig)),
                "promotional_price": str(int(p))
            })
        else:
            r = _put(f"{API_BASE}/products/{pid}", {
                "price": str(int(p)),
                "promotional_price": ""
            })
        ok = r and r.status_code in (200, 201)
        if not ok: print(f"  ⚠️ precio producto {pid}: HTTP {r.status_code if r is not None else 'None'}")
        time.sleep(0.4)
        if ok: _actualizar_envio_gratis_prod(prod, p)
        return ok, p, p

    vars_prov = datos_prov.get("variantes", {})
    precios   = {}
    for v in variantes:
        costo = None; costo_ant = 0; v_oferta = False
        vnom = normalizar(v["nombre"])
        for pv_norm, pv_datos in vars_prov.items():
            if match(vnom, pv_norm):
                costo     = pv_datos["precio"]
                costo_ant = pv_datos.get("precio_anterior", 0)
                v_oferta  = pv_datos.get("en_oferta", False)
                break
        if costo is None:
            costo     = datos_prov["precio_base"]
            costo_ant = precio_anterior
            v_oferta  = en_oferta
        precios[v["id"]] = {
            "precio":          precio_obj(costo),
            "precio_anterior": precio_obj(costo_ant) if costo_ant else 0,
            "en_oferta":       v_oferta
        }
    exitos = 0
    for vid, pd in precios.items():
        p = pd["precio"]
        if pd["en_oferta"] and pd["precio_anterior"] > 0:
            data = {"price": str(int(pd["precio_anterior"])), "promotional_price": str(int(p))}
        else:
            data = {"price": str(int(p)), "promotional_price": ""}
        r = _put(f"{API_BASE}/variants/{vid}", data)
        if r and r.status_code in (200, 201): exitos += 1
        else: print(f"  ⚠️ precio variante {vid}: HTTP {r.status_code if r is not None else 'None'}")
        time.sleep(0.4)
    if not precios or exitos == 0: return False, 0, 0
    precio_min = min(pd["precio"] for pd in precios.values())
    precio_max = max(pd["precio"] for pd in precios.values())
    _actualizar_envio_gratis_prod(prod, precio_min)
    return True, precio_min, precio_max

# ══════════════════════════════════════════════════════════════════════════════
# SINCRONIZACIÓN DE STOCK REAL
# ══════════════════════════════════════════════════════════════════════════════
def sincronizar_stock(prod, datos_prov, sinc):
    if not _token: return
    nombre_real = prod["nombre"]
    pid         = prod["id"]
    variantes   = prod["variantes"]
    vars_prov   = datos_prov.get("variantes", {})
    stock_base  = datos_prov.get("stock_base", 0)

    if stock_base >= 9999:
        # Stock ilimitado — pero restaurar si estaba en 0
        necesita_restaurar = sinc.get(nombre_real,{}).get("sin_stock") or sinc.get(nombre_real,{}).get("stock_sinc") == 0
        if necesita_restaurar:
            if variantes:
                for v in variantes:
                    set_stock_variante(v["id"], 10)
                    sinc.setdefault(nombre_real,{})[f"stock_{v['id']}"] = 10
                    time.sleep(0.3)
            else:
                set_stock_producto(pid, 10)
                sinc.setdefault(nombre_real,{})["stock_sinc"] = 10
            sinc.get(nombre_real,{}).pop("sin_stock", None)
            print(f"   🔄 Stock restaurado: {nombre_real}")
        return

    if variantes:
        for v in variantes:
            vnom  = normalizar(v["nombre"])
            stock = stock_base
            for pv_norm, pv_datos in vars_prov.items():
                if match(vnom, pv_norm):
                    stock = pv_datos.get("stock", stock_base); break
            ultimo = sinc.get(nombre_real,{}).get(f"stock_{v['id']}")
            if ultimo != stock:
                set_stock_variante(v["id"], stock)
                sinc.setdefault(nombre_real,{})[f"stock_{v['id']}"] = stock
            time.sleep(0.3)
    else:
        ultimo = sinc.get(nombre_real,{}).get("stock_sinc")
        if ultimo != stock_base:
            set_stock_producto(pid, stock_base)
            sinc.setdefault(nombre_real,{})["stock_sinc"] = stock_base

def marcar_sin_stock(prod):
    pid = prod["id"]; variantes = prod["variantes"]
    if variantes:
        ok = True
        for v in variantes:
            if not set_stock_variante(v["id"], 0): ok = False
            time.sleep(0.3)
        return ok
    else:
        r = _put(f"{API_BASE}/products/{pid}", {"stock":0,"stock_management":True})
        if not (r and r.status_code in (200,201)):
            print(f"⚠️ No pude poner stock=0 en producto {pid} — queda visible")
            return True
        return True

# ══════════════════════════════════════════════════════════════════════════════
# SYNC TOTAL
# ══════════════════════════════════════════════════════════════════════════════
def run_sync_total():
    db   = leer_db()
    prov = db.get("productos_proveedor",{})
    sinc = db.get("sincronizados",{})
    if not prov:
        tg("⚠️ Sin datos del proveedor. Esperá un ciclo de monitoreo primero."); return
    catalogo = obtener_catalogo(forzar=True)
    if not catalogo:
        tg("❌ No pude obtener el catálogo de la API."); return
    db = leer_db()
    sinc = db.get("sincronizados", sinc)
    idx   = construir_indice(prov)
    total = len(catalogo)
    act_precios   = []
    act_sin_stock = []
    errores       = []
    tg(f"🔄 *{_nt('Sync total iniciada')}*\n{total} productos a procesar...")
    for i, prod in enumerate(catalogo):
        nombre_real = prod["nombre"]
        precio_web  = prod["precio_base"]
        _, datos_prov = buscar_en_indice(prod["nombre_norm"], idx)
        if datos_prov is None:
            if marcar_sin_stock(prod):
                sinc[nombre_real] = {"sin_stock": True}
                act_sin_stock.append(f"• *{nombre_real}*")
            else:
                errores.append(nombre_real)
        else:
            if sinc.get(nombre_real,{}).get("sin_stock"):
                precio_guardado = sinc[nombre_real].get("precio",0)
                sinc[nombre_real] = {"precio":precio_guardado} if precio_guardado else {}
            ok, p_min, p_max = sincronizar_precios(prod, datos_prov)
            if ok:
                sinc.setdefault(nombre_real,{})["precio"] = p_min
                if int(precio_web) != p_min:
                    rango = f"${p_min:,}" if p_min==p_max else f"${p_min:,}–${p_max:,}"
                    etiq  = f"({len(prod['variantes'])} var.)" if prod["tiene_variantes"] else "(sin var.)"
                    act_precios.append(f"• *{nombre_real}* {etiq}\n  Antes: ${int(precio_web):,} → *Nuevo: {rango}*")
            else:
                errores.append(nombre_real)
            sincronizar_stock(prod, datos_prov, sinc)
        if (i+1) % 50 == 0: print(f"   Sync [{i+1}/{total}]")
    db["sincronizados"] = sinc
    escribir_db(db)
    resumen = (f"✅ *{_nt('Sync total terminada')}*\n\n"
               f"📊 Total: *{total}*\n"
               f"💲 Precios actualizados: *{len(act_precios)}*\n"
               f"📦 Sin stock: *{len(act_sin_stock)}*\n")
    if errores: resumen += f"⚠️ Errores: *{len(errores)}*"
    tg(resumen)
    for i in range(0, len(act_precios), 20):
        tg(f"💲 *Precios actualizados:*\n\n" + "\n\n".join(act_precios[i:i+20]))
    for i in range(0, len(act_sin_stock), 30):
        tg(f"📦 *Marcados sin stock:*\n\n" + "\n".join(act_sin_stock[i:i+30]))

# ══════════════════════════════════════════════════════════════════════════════
# GMAIL
# ══════════════════════════════════════════════════════════════════════════════
def chequear_gmail():
    if not GMAIL_USER or not GMAIL_PASS: return []
    pedidos = []; conn = None
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com")
        conn.login(GMAIL_USER, GMAIL_PASS); conn.select("inbox")
        st, msgs = conn.search(None, '(UNSEEN FROM "tiendanegocio.com")')
        if st != "OK" or not msgs[0]: conn.close(); conn.logout(); return []
        for mid in msgs[0].split():
            sid = mid.decode()
            res, data = conn.fetch(mid, "(RFC822)")
            if res != "OK": continue
            msg = email.message_from_bytes(data[0][1])
            subj, enc = decode_header(msg["Subject"])[0]
            if isinstance(subj, bytes): subj = subj.decode(enc or "utf-8")
            if not any(p in subj.lower() for p in ["compra","realiz","pedido","venta"]): continue
            num_m = re.search(r"#(\d+)", subj)
            num = num_m.group(1) if num_m else sid
            cuerpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type()=="text/plain":
                        cuerpo = part.get_payload(decode=True).decode("utf-8","ignore"); break
            else:
                cuerpo = msg.get_payload(decode=True).decode("utf-8","ignore")
            items = _parsear_items(cuerpo)
            if items:
                pedidos.append({"id":sid,"num":num,"items":items})
                conn.store(mid, "+FLAGS", "\\Seen")
        conn.close(); conn.logout()
    except Exception as e:
        print("Gmail: " + str(e))
        try:
            if conn: conn.close(); conn.logout()
        except Exception: pass
    return pedidos

def _parsear_items(cuerpo):
    items=[]; en=False
    for l in cuerpo.split('\n'):
        l=l.strip()
        if "productos:" in l.lower(): en=True; continue
        if en:
            if not l or "subtotal:" in l.lower(): en=False; continue
            if l.startswith('-'):
                m=re.match(r'-\s*(.+?)\s+x(\d+)\s*-',l)
                if m: items.append({"nombre":m.group(1),"cant":int(m.group(2))})
    return items

# ══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ══════════════════════════════════════════════════════════════════════════════
def generar_excel():
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return None, "❌ Falta openpyxl en requirements.txt"
    db  = leer_db(); prov = db.get("productos_proveedor",{})
    if not prov: return None, "❌ Sin datos del proveedor."
    catalogo = obtener_catalogo()
    if not catalogo: return None, "❌ Sin catálogo de la API."
    idx = construir_indice(prov)
    cols = ["Hash","Nombre del producto","Precio","Oferta","Stock",
            "Visibilidad (Visible o Oculto)","Descripción","SKU",
            "Peso en KG","Alto en CM","Ancho en CM","Profundidad en CM",
            "Nombre de variante #1","Opción de variante #1",
            "Nombre de variante #2","Opción de variante #2",
            "Nombre de variante #3","Opción de variante #3",
            "Categorías > Subcategorías > … > Subcategorías"]
    filas=[]; act=ign=sin=0
    for prod in catalogo:
        _,dp = buscar_en_indice(prod["nombre_norm"], idx)
        if dp is None: sin+=1; continue
        p = precio_obj(dp["precio_base"])
        if p == int(prod["precio_base"]): ign+=1; continue
        f={c:"" for c in cols}
        f["Hash"]=prod["nombre_norm"]; f["Nombre del producto"]=prod["nombre"]; f["Precio"]=p
        filas.append(f); act+=1
    if not filas: return None, f"ℹ️ Sin cambios.\n• {ign} ya correctos\n• {sin} sin match"
    wb=openpyxl.Workbook(); ws=wb.active; ws.title="Productos"
    ws.append(cols)
    hf=PatternFill("solid",fgColor="1F6B3B"); hfnt=Font(bold=True,color="FFFFFF",name="Arial",size=10)
    for c in ws[1]: c.fill=hf; c.font=hfnt; c.alignment=Alignment(horizontal="center",vertical="center")
    ws.row_dimensions[1].height=25
    fills=[PatternFill("solid",fgColor="F0F7F2"),PatternFill("solid",fgColor="FFFFFF")]
    fn=Font(name="Arial",size=9); fv=Font(name="Arial",size=9,bold=True,color="1F6B3B")
    for i,fila in enumerate(filas,2):
        ws.append([fila[c] for c in cols])
        for cell in ws[i]: cell.fill=fills[i%2]; cell.font=fn
        ws.cell(row=i,column=3).font=fv; ws.cell(row=i,column=3).number_format='#,##0'
    for col,w in zip("ABCDEFGHIJKLMNOPQRS",[38,48,12,8,8,12,8,8,8,8,8,8,20,22,20,22,20,22,30]):
        ws.column_dimensions[col].width=w
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.getvalue(),(f"✅ Excel:\n• *{act}* a actualizar\n• *{ign}* ya correctos\n• *{sin}* sin match\n\n"
                           f"Importalo: *Productos → Importar y exportar → Importar*")

# ══════════════════════════════════════════════════════════════════════════════
# CICLO DE MONITOREO
# ══════════════════════════════════════════════════════════════════════════════

def chequear_ordenes_api():
    if not _token: return []
    try:
        db  = leer_db()
        ultimo_id = db.get("ultimo_orden_id", 0)
        params = {"per_page": 50}
        if ultimo_id:
            params["since_id"] = ultimo_id
        r = _get(f"{API_BASE}/orders", params=params)
        if r is None or r.status_code != 200:
            detalle = r.text[:150] if r is not None else "Sin respuesta"
            print(f"⚠️ Órdenes API: HTTP {r.status_code if r is not None else 'None'} — {detalle}")
            return []
        data    = r.json()
        ordenes = data.get("results", data) if isinstance(data, dict) else data
        if not isinstance(ordenes, list) or not ordenes:
            return []
        max_id = max(o.get("id", 0) for o in ordenes)
        if ultimo_id == 0:
            db["ultimo_orden_id"] = max_id
            ya_proc = db.get("pedidos_procesados", [])
            for o in ordenes:
                oid = str(o.get("id", ""))
                if oid and oid not in ya_proc:
                    ya_proc.append(oid)
            db["pedidos_procesados"] = ya_proc
            escribir_db(db)
            print(f"   Ordenes API: primer ciclo — {len(ordenes)} ordenes marcadas como procesadas")
            return []
        if max_id > ultimo_id:
            db["ultimo_orden_id"] = max_id
            escribir_db(db)
        print(f"   Ordenes API: {len(ordenes)} desde ID {ultimo_id}")
        return ordenes
    except Exception as e:
        print(f"❌ chequear_ordenes_api: {e}")
        return []

def notificar_orden_nueva(orden):
    num      = str(orden.get("number", orden.get("id", "?")))
    productos = orden.get("products", [])
    total    = float(orden.get("total") or 0)
    pago_st  = orden.get("payment_status", "?")
    envio    = orden.get("shipping", {})
    metodo   = envio.get("pickup_type", "") or envio.get("method", "") or ""
    db   = leer_db()
    prov = db.get("productos_proveedor", {})
    msg  = "🛒 *" + _nt("¡Nuevo Pedido #" + num + "!") + "*\n\n"
    msg += f"💰 Total: *${total:,.0f}*\n"
    msg += f"💳 Pago: *{pago_st}*\n"
    if metodo:
        msg += f"📦 Envío: *{metodo}*\n"
    msg += "\n"
    if prov:
        idx = construir_indice(prov)
        for item in productos:
            nombre   = item.get("name", "?")
            cantidad = item.get("quantity", 1)
            _, dp    = buscar_en_indice(normalizar(nombre), idx)
            if dp:
                stock = dp.get("stock_base", 0)
                icono = "⚠️" if stock < cantidad else "✅"
                extra = f" (stock proveedor: {stock})" if stock < cantidad else ""
                msg += f"{icono} *{nombre}* x{cantidad}{extra}\n"
            else:
                msg += f"❓ *{nombre}* x{cantidad}\n"
    else:
        for item in productos:
            msg += f"• *{item.get('name','?')}* x{item.get('quantity',1)}\n"
    tg(msg)

_ciclo_lock = threading.Lock()
RECONCILIAR_H = 6   # cada X horas se manda el catálogo completo a la web (por si algo quedó distinto); el resto, solo lo que cambió

def _firma(p):
    """Lo que importa comparar de un producto para saber si hay que mandarlo a la web."""
    return f"{p.get('precio_base')}|{p.get('precio_oferta')}|{p.get('stock')}|{p.get('costo')}"

_ciclo_desde = {"t": 0}

def ciclo_monitoreo():
    """Un ciclo completo. Devuelve False si ya había otro corriendo (no se pisan)."""
    if not _ciclo_lock.acquire(blocking=False):
        print("⏳ Ya hay un ciclo corriendo — se saltea.")
        return False
    _ciclo_desde["t"] = time.time()
    try:
        _ciclo_monitoreo()
        return True
    finally:
        _ciclo_desde["t"] = 0
        _ciclo_lock.release()
        resolver("trabado")

LATIDO_MIN = 10
HEALTHCHECK_URL = _e("HEALTHCHECK_URL")   # monitor externo (healthchecks.io): si deja de recibir el ping, avisa por Telegram

def ping_externo():
    if not HEALTHCHECK_URL: return
    try: requests.get(HEALTHCHECK_URL, timeout=10)
    except Exception as e: print(f"⚠️ ping externo: {e}")

def hilo_latido():
    """Cada 10 min: latido a la web (si deja de llegar ~45 min, la web avisa que el bot se cayó)
    y aviso si un ciclo lleva demasiado tiempo corriendo (trabado)."""
    while True:
        try:
            t = _ciclo_desde["t"]
            if t and time.time() - t > 45 * 60:
                alerta("trabado", "Un ciclo del bot está trabado",
                       f"Lleva {_dur(time.time() - t)} corriendo (lo normal es 1 a 5 min). Si sigue así, reiniciá el bot en Railway.")
            enviar_latido()
            ping_externo()
        except Exception as e: print(f"⚠️ latido: {e}")
        time.sleep(LATIDO_MIN * 60)

def _registrar_ciclo(inicio, ok, motivo="", **extra):
    n_pedidos = _diag.get("n_thordata", 0)
    def _f(e):
        e["ultimo_ciclo"] = {"at": time.time(), "dur": round(time.time() - inicio), "ok": ok, "motivo": motivo,
                             "fuente": _diag.get("fuente", ""), "pedidos": n_pedidos,
                             "modo": getattr(scrapear_proveedor, "modo", ""), **extra}
        dia = e["dia"]
        dia["ciclos_ok" if ok else "ciclos_fallidos"] = dia.get("ciclos_ok" if ok else "ciclos_fallidos", 0) + 1
        dia["pedidos"] = dia.get("pedidos", 0) + n_pedidos
        # Consumo del Web Unlocker por día (últimos 8 días) para estimar el gasto del mes
        hoy = _ahora_ar().strftime("%Y-%m-%d")
        c = e.setdefault("consumo", {})
        c[hoy] = c.get(hoy, 0) + n_pedidos
        for k in sorted(c)[:-8]: c.pop(k, None)
    _mod_estado(_f)

def _minutos_ciclo():
    """Cada cuánto corre el próximo ciclo: CICLO_MINUTOS de día, CICLO_NOCHE_MIN de noche (sin pasarse de las NOCHE_HASTA)."""
    ahora = _ahora_ar()
    h = ahora.hour
    noche = (NOCHE_DESDE <= h or h < NOCHE_HASTA) if NOCHE_DESDE > NOCHE_HASTA else (NOCHE_DESDE <= h < NOCHE_HASTA)
    if not noche or CICLO_NOCHE_MIN <= CICLO_MINUTOS:
        return CICLO_MINUTOS
    fin = ahora.replace(hour=NOCHE_HASTA, minute=0, second=0, microsecond=0)
    if fin <= ahora: fin += timedelta(days=1)
    return max(CICLO_MINUTOS, min(CICLO_NOCHE_MIN, int((fin - ahora).total_seconds() // 60) + 1))

def texto_consumo():
    """'X pedidos hoy · ~Y por día → ~Z por mes (≈ USD W)' del Web Unlocker."""
    c = leer_estado().get("consumo", {})
    if not c: return "todavía sin datos"
    hoy = _ahora_ar().strftime("%Y-%m-%d")
    dias_completos = [v for k, v in c.items() if k != hoy]
    prom = (sum(dias_completos) / len(dias_completos)) if dias_completos else None
    txt = f"{c.get(hoy, 0)} pedidos hoy"
    if prom is not None:
        mes = prom * 30
        txt += f" · promedio {prom:.0f} por día → ~{mes:,.0f} por mes (≈ USD {mes * THORDATA_USD_1K / 1000:.0f})".replace(",", ".")
    return txt

def _sumar_dia(**kw):
    """Acumula números para el resumen diario."""
    def _f(e):
        dia = e["dia"]
        for k, v in kw.items():
            if isinstance(v, list): dia[k] = (dia.get(k) or []) + v
            else: dia[k] = dia.get(k, 0) + v
    _mod_estado(_f)

def revisar_servicios_proveedor(hubo_datos):
    """Después de leer el proveedor: avisa si ThorData / el proxy / ScraperAPI se quedaron sin crédito,
    o si hace varios ciclos que no se puede leer el catálogo. Y avisa cuando vuelve a andar."""
    d = {k: dict(v) for k, v in list(_diag.items()) if isinstance(v, dict)}
    th, px, sc = d.get("thordata", {}), d.get("proxy", {}), d.get("scraperapi", {})
    if THORDATA_TOKEN:
        if th.get("credito") and not th.get("ok"):
            alerta("thordata", "ThorData sin crédito o con la clave vencida",
                   "Es el servicio que lee el catálogo del proveedor." +
                   (" El bot siguió con el proxy residencial, que gasta más." if hubo_datos else "") +
                   f"\nÚltimo error: {_md(th.get('ultimo', ''))}\nCargá saldo en ThorData o revisá `THORDATA_TOKEN` en Railway.")
        elif th.get("ok"):
            resolver("thordata")
        # Falla por otro motivo varios ciclos seguidos (el bot sigue con el proxy, pero conviene saberlo)
        n_th = _mod_estado(lambda e: e.__setitem__("fallos_thordata", 0 if th.get("ok") else e.get("fallos_thordata", 0) + (1 if th.get("fallas") else 0)) or e["fallos_thordata"])
        if n_th >= 4 and not th.get("credito"):
            alerta("thordata_falla", "ThorData viene fallando",
                   f"Hace {n_th} ciclos que no responde bien" + (" y el bot usa el proxy residencial." if hubo_datos else ".") +
                   f"\nÚltimo error: {_md(th.get('ultimo', ''))}", grave=False)
        elif th.get("ok"):
            resolver("thordata_falla")
    if px.get("credito") and not px.get("ok"):
        alerta("proxy", "Proxy residencial sin saldo o con datos inválidos",
               f"Último error: {_md(px.get('ultimo', ''))}\nRevisá el saldo del proxy y `PROXY_USER` / `PROXY_PASS` en Railway.")
    elif px.get("ok"):
        resolver("proxy")
    elif hubo_datos and not px:
        resolver("proxy", avisar=False)   # no hizo falta usarlo: si sigue mal, avisa la próxima vez que se use
    ya_avisado = any(k.startswith("scraperapi_") for k in leer_estado().get("alertas", {}))
    if sc.get("credito") and not sc.get("ok") and not ya_avisado:
        alerta("scraperapi", "ScraperAPI sin créditos", "Es el último respaldo para leer el proveedor.", grave=False)
    elif ya_avisado:
        resolver("scraperapi", avisar=False)
    elif sc.get("ok"):
        resolver("scraperapi")
    elif hubo_datos and not sc:
        resolver("scraperapi", avisar=False)
    n = _mod_estado(lambda e: e.__setitem__("fallos_proveedor", 0 if hubo_datos else e.get("fallos_proveedor", 0) + 1) or e["fallos_proveedor"])
    if hubo_datos:
        resolver("proveedor")
    elif n >= 2:
        motivos = "\n".join(f"• {s}: {_md(v.get('ultimo', ''))}" for s, v in d.items() if v.get("ultimo"))
        alerta("proveedor", "No puedo leer el catálogo del proveedor",
               f"Van {n} ciclos seguidos sin datos (unos {n * CICLO_MINUTOS} min). La web queda con los últimos precios y stock."
               + (f"\n{motivos}" if motivos else ""))

def _bloque(titulo, lineas, max_lineas=15):
    if not lineas: return ""
    extra = f"\n_...y {len(lineas) - max_lineas} más_" if len(lineas) > max_lineas else ""
    return f"\n\n*{titulo}*\n" + "\n".join(lineas[:max_lineas]) + extra

def _pesos(n):
    return "$" + f"{int(n):,}".replace(",", ".")

def _pl(n, uno, varios):
    return f"{n} {uno if n == 1 else varios}"

FUENTES = {"thordata": "ThorData", "proxy": "proxy residencial", "scraperapi": "ScraperAPI"}

def _ciclo_monitoreo():
    inicio = time.time()
    print(f"\n─── 🔄 Ciclo {_ahora_ar().strftime('%H:%M')} ───")
    db = leer_db()

    # ── Botón ON/OFF: si el scraping está apagado, no salir a RXZ ──
    if not db.get("scraping_activo", False):
        print("⏸️  Scraping APAGADO — encendé con /encender (o botón del menú).")
        return

    prov_ant = db.get("productos_proveedor", {})
    excluidas = db.get("categorias_excluidas", CATEGORIAS_EXCLUIDAS_DEFAULT)

    # 1) Traer catálogo del proveedor (fuente de verdad), filtrando categorías excluidas
    prov_nuevo = scrapear_proveedor(excluidas)
    completo = bool(getattr(scrapear_proveedor, "completo", False)) and bool(prov_nuevo)
    revisar_servicios_proveedor(bool(prov_nuevo))
    if not prov_nuevo:
        # Sin datos: no se toca la web (queda con lo último) y se reintenta el próximo ciclo
        print("⚠️ Proveedor sin datos — la web queda con el último estado.")
        _registrar_ciclo(inicio, False, "no se pudo leer el proveedor")
        return
    n_ref = db.get("ultimo_completo_n", 0)
    if completo and n_ref and len(prov_nuevo) < n_ref * 0.7:
        print(f"⚠️ El proveedor trajo {len(prov_nuevo)} productos (la última lectura completa trajo {n_ref}): se toma como incompleta.")
        completo = False
    n_inc = _mod_estado(lambda e: e.__setitem__("lecturas_incompletas", 0 if completo else e.get("lecturas_incompletas", 0) + 1) or e["lecturas_incompletas"])
    if n_inc >= 4:
        alerta("incompleta", "El catálogo del proveedor llega incompleto",
               f"Hace {n_inc} ciclos que no llega entero ({len(prov_nuevo)} productos; la última lectura completa trajo {n_ref or '?'}). "
               "Mientras tanto no se marca nada sin stock por faltante.", grave=False)
    elif completo:
        resolver("incompleta")

    vendidos = vendidos_30d()   # {sku: {nombre, unidades}} — para avisar solo lo que importa
    bloques = ""

    # Categorías nuevas del proveedor
    cats_ahora = getattr(scrapear_proveedor, "ultimas_categorias", [])
    cats_conocidas = db.get("categorias_conocidas", [])
    cats_guardar = sorted(set(cats_conocidas) | set(cats_ahora)) if cats_ahora else cats_conocidas
    if cats_conocidas:  # no avisar en el primer ciclo (todo sería "nuevo")
        cats_nuevas = [c for c in cats_ahora if c not in cats_conocidas]
        if cats_nuevas:
            lin = [f"• {_md(c)}" + (" (excluida, no se carga)" if _categoria_excluida(c, excluidas) else "") for c in cats_nuevas]
            bloques += _bloque("🆕 Categorías nuevas en el proveedor", lin)

    prov_consolidado = {**prov_ant, **prov_nuevo}

    # 2) Ofertas nuevas, stock bajo y cambios de precio (comparando con el ciclo anterior del proveedor)
    ofertas_nuevas = {}
    lineas_stock_bajo = []
    for clave, datos in prov_nuevo.items():
        viejo = prov_ant.get(clave, {})
        if datos.get("en_oferta") and datos.get("precio_anterior", 0) > datos["precio"] and viejo and not viejo.get("en_oferta", False):
            base = datos.get("nombre_base_proveedor", clave)
            if base not in ofertas_nuevas:
                ofertas_nuevas[base] = datos
        # Stock bajo: solo cuando RECIÉN baja (no cada ciclo) y solo de lo que se vende
        sku = f"RXZ-{datos.get('woo_id')}"
        stock, stock_ant = datos.get("stock", 0) or 0, viejo.get("stock", 0) or 0
        if viejo and sku in vendidos and 0 < stock <= ALERTA_STOCK < stock_ant and stock < 9999:
            lineas_stock_bajo.append(f"• {_md(datos['nombre_real'])}: quedan {int(stock)} (vendiste {vendidos[sku].get('unidades', 0)} en 30 días)")

    # 3) Armar el LOTE para ComerciApp (upsert por SKU = RXZ-{woo_id})
    lote = []
    cambios_precio, subas_fuertes = [], []
    n_subas = n_bajas = 0
    for clave, datos in prov_nuevo.items():
        woo_id = datos.get("woo_id")
        if not woo_id:
            continue
        sku = f"RXZ-{woo_id}"
        costo = datos.get("precio", 0)
        if costo <= 0:
            continue
        precio_venta = precio_obj(costo)
        stock = datos.get("stock", 0) or 0
        en_oferta = datos.get("en_oferta", False)
        precio_anterior = datos.get("precio_anterior", 0)

        # Precio y oferta: en la web nueva precio_base = precio normal, precio_oferta = precio con descuento
        precio_base = precio_venta
        precio_oferta = 0
        if en_oferta and precio_anterior > 0:
            precio_base = precio_obj(precio_anterior)   # precio "regular" tachado
            precio_oferta = precio_venta                # precio con descuento

        envio_gratis = precio_venta >= ENVIO_GRATIS_MIN and int(woo_id) not in PRODUCTOS_PESADOS_IDS

        lote.append({
            "sku": sku,
            "nombre": datos.get("nombre_real", ""),
            "precio_base": precio_base,
            "precio_oferta": precio_oferta,
            "stock": int(stock) if stock else 0,
            "imagen": datos.get("imagen", ""),
            "imagenes": datos.get("imagenes", []),
            "categoria": datos.get("categoria", ""),
            "descripcion": datos.get("descripcion", ""),
            "peso": datos.get("peso", 0),
            "alto": datos.get("alto", 0),
            "ancho": datos.get("ancho", 0),
            "largo": datos.get("largo", 0),
            "envio_gratis": envio_gratis,
            "costo": costo,   # lo que cobra rxz → precio de costo en la web (ganancia real en el dashboard)
        })

        # Cambio de precio del proveedor (comparando con el ciclo anterior)
        costo_viejo = prov_ant.get(clave, {}).get("precio", 0)
        if costo_viejo and costo_viejo != costo:
            pv_viejo = precio_obj(costo_viejo)
            if pv_viejo != precio_venta and pv_viejo > 0:
                pct = (precio_venta - pv_viejo) * 100 / pv_viejo
                linea = f"{'🔼' if pct > 0 else '🔽'} {_md(datos['nombre_real'])}: {_pesos(pv_viejo)} → {_pesos(precio_venta)} ({pct:+.0f}%)"
                if pct > 0: n_subas += 1
                else: n_bajas += 1
                if datos.get("nombre_base_proveedor", clave) in ofertas_nuevas:
                    pass   # ya sale en "Ofertas nuevas"
                elif pct >= SUBA_ALERTA_PCT:
                    vend = vendidos.get(sku)
                    subas_fuertes.append(linea + (f" · vendiste {vend.get('unidades', 0)} en 30 días" if vend else ""))
                else:
                    cambios_precio.append(linea)

    # 4) Mandar a la web SOLO lo que cambió (y cada RECONCILIAR_H horas, todo)
    skus_web = comerciapp_skus_existentes()
    if skus_web is None:
        n = _mod_estado(lambda e: e.__setitem__("fallos_web", e.get("fallos_web", 0) + 1) or e["fallos_web"])
        if n >= 2:
            alerta("web", "No puedo conectarme con la web",
                   f"Van {n} ciclos sin poder actualizar la web ({_md(getattr(comerciapp_skus_existentes, 'error', '') or 'sin respuesta')}). "
                   "Revisá el servicio de la API en Railway.")
        _registrar_ciclo(inicio, False, "la web no responde", productos=len(prov_nuevo))
        return   # no se guarda el proveedor: los cambios se avisan cuando la web vuelva
    enviado = db.get("enviado_web", {})
    reconciliar = (time.time() - db.get("reconciliado_at", 0) > RECONCILIAR_H * 3600) or not enviado
    lote_envio = [p for p in lote if reconciliar or p["sku"] not in skus_web or enviado.get(p["sku"]) != _firma(p)
                  or (skus_web.get(p["sku"]) or 0) != p["stock"]]   # ej. la web descontó una venta: vuelve al stock del proveedor
    ocultar_nuevos = bool(db.get("aprobar_nuevos", True)) and len(skus_web) >= 20
    resumen = {"insertados": 0, "actualizados": 0, "errores": 0, "ok_skus": [], "sin_stock": [], "con_stock": [], "nuevos": []}
    web_ok = True
    if lote_envio:
        print(f"   📤 A la web: {len(lote_envio)} de {len(lote)} productos{' (reconciliación completa)' if reconciliar else ' (solo los que cambiaron)'}")
        r = comerciapp_sync(lote_envio, ocultar_nuevos)
        if r is None:
            web_ok = False
            n = _mod_estado(lambda e: e.__setitem__("fallos_web", e.get("fallos_web", 0) + 1) or e["fallos_web"])
            if n >= 2:
                alerta("web", "No puedo actualizar la web", f"Van {n} ciclos en que la web no acepta los cambios. Revisá el servicio de la API en Railway.")
        else:
            resumen = r
            print(f"   ✅ ComerciApp: {r.get('insertados',0)} nuevos, {r.get('actualizados',0)} con cambios, {r.get('errores',0)} errores")
            if r.get("errores", 0) > 0:
                pe = r.get("primer_error", "")
                ejemplos = "\n".join(f"• {_md(d.get('sku','?'))}: {_md(d.get('error','?'))}" for d in (r.get("detalles") or [])[:3])
                alerta("web_errores", f"La web rechazó {r.get('errores')} productos",
                       (f"Error principal: {_md(pe)}\n" if pe else "") + ejemplos, grave=False)
            else:
                resolver("web_errores")
    else:
        print(f"   ✅ Sin cambios para la web ({len(lote)} productos iguales)")
    if web_ok:
        _mod_estado(lambda e: e.__setitem__("fallos_web", 0))
        resolver("web")
    for sku in resumen.get("ok_skus", []):
        p = next((x for x in lote_envio if x["sku"] == sku), None)
        if p: enviado[sku] = _firma(p)

    # 5) Productos que se CAYERON del proveedor → stock 0 en la web (solo si la lectura fue completa)
    caidos, afectados = [], 0
    if completo and skus_web:
        skus_proveedor = {f"RXZ-{d.get('woo_id')}" for d in prov_nuevo.values() if d.get("woo_id")}
        caidos = [sku for sku, stock_web in skus_web.items() if sku not in skus_proveedor and (stock_web or 0) > 0]
        con_stock_web = sum(1 for v in skus_web.values() if (v or 0) > 0)
        forzar = bool(db.get("forzar_caidos"))
        if len(caidos) > max(20, con_stock_web * 0.15) and not forzar:
            # Demasiados faltantes de golpe: más probable una lectura rara que un cambio real. No se toca nada.
            alerta("caidos", "Muchos productos desaparecieron del proveedor de golpe",
                   f"Faltan {len(caidos)} de {con_stock_web} con stock. Por seguridad NO los marqué sin stock. "
                   "Si es real (el proveedor los sacó), mandá /forzar\\_caidos y en el próximo ciclo se marcan.", grave=False)
            caidos = []
        elif caidos or completo:
            resolver("caidos", avisar=False)
        if caidos:
            afectados = comerciapp_stock_cero(caidos)
            for sku in caidos: enviado.pop(sku, None)
            print(f"   📉 {afectados} productos caídos del proveedor → stock 0")
    elif not completo:
        print("   ⚠️ Lectura incompleta del proveedor: no se marca nada sin stock por faltante.")

    # 6) Un solo mensaje por ciclo, y solo si hay algo que contar
    partes = []
    n_cambios = resumen.get("actualizados", 0) or 0
    if n_cambios:
        det = [f"{resumen[k]} de {t}" for k, t in (("cambios_stock", "stock"), ("cambios_precio", "precio"), ("cambios_costo", "costo")) if resumen.get(k)]
        partes.append(f"🔄 {n_cambios} {'producto actualizado' if n_cambios == 1 else 'productos actualizados'}" + (f" ({', '.join(det)})" if det else ""))
    if resumen.get("insertados"):
        partes.append(f"🆕 {resumen['insertados']} nuevos" + (" (ocultos, esperan tu aprobación)" if ocultar_nuevos else ""))
    if afectados:
        partes.append(f"📉 {afectados} sin stock (ya no están en el proveedor)")

    # Lo que se vende y se quedó sin stock / volvió (lo demás va al resumen diario)
    sin_stock_vend = [x for x in resumen.get("sin_stock", []) if x.get("sku") in vendidos]
    sin_stock_vend += [{"sku": s, "nombre": vendidos[s].get("nombre", s)} for s in caidos if s in vendidos]
    con_stock_vend = [x for x in resumen.get("con_stock", []) if x.get("sku") in vendidos]
    bloques += _bloque(f"🚨 Subas fuertes del proveedor ({SUBA_ALERTA_PCT:.0f}% o más)", subas_fuertes)
    bloques += _bloque("⛔ Sin stock en el proveedor (los vendiste en los últimos 30 días)",
                       [f"• {_md(x.get('nombre'))} (vendiste {vendidos.get(x['sku'], {}).get('unidades', 0)})" for x in sin_stock_vend])
    bloques += _bloque("📦 Quedan pocas unidades en el proveedor", lineas_stock_bajo)
    bloques += _bloque("✅ Volvieron a tener stock", [f"• {_md(x.get('nombre'))}" for x in con_stock_vend])
    bloques += _bloque("💲 Cambios de precio", cambios_precio)
    if ofertas_nuevas:
        bloques += _bloque("🏷️ Ofertas nuevas del proveedor",
                           [f"{i+1}. {_md(d['nombre_real'])}: {_pesos(precio_obj(d.get('precio_anterior', 0)))} → {_pesos(precio_obj(d['precio']))}"
                            for i, d in enumerate(ofertas_nuevas.values())])

    # Fotos nuevas (o rotas) del proveedor → Cloudinary, con tope de tiempo por ciclo
    n_fotos = 0
    if FOTOS_SEG_POR_CICLO > 0:
        try:
            rf = reparar_fotos(tiempo_max=FOTOS_SEG_POR_CICLO)
            if rf and rf.get("ok"):
                n_fotos = rf["ok"]
                print(f"   📸 {rf['ok']} fotos subidas a Cloudinary (quedan {rf['pendientes']})")
                partes.append(f"📸 {rf['ok']} fotos subidas" + (f" (quedan {rf['pendientes']})" if rf['pendientes'] else ""))
                resolver("fotos")
            elif rf and rf.get("fallidas", 0) >= 10:
                top = sorted(rf.get("motivos", {}).items(), key=lambda x: -x[1])[:3]
                alerta("fotos", "No se pueden subir las fotos del proveedor a la web",
                       f"{rf['fallidas']} fallaron este ciclo.\n" + "\n".join(f"• {_md(k)}: {v}" for k, v in top), grave=False)
        except Exception as e:
            print(f"⚠️ Fotos: {e}")

    if bloques or resumen.get("insertados"):
        tg(f"🔄 *{_nt('Novedades ' + _ahora_ar().strftime('%H:%M'))}*\n" + " | ".join(partes) + bloques)
    else:
        print(f"✅ Sin novedades para avisar ({' | '.join(partes) or 'sin cambios'}).")
    if resumen.get("nuevos") and ocultar_nuevos:
        try: avisar_nuevos(resumen["nuevos"])
        except Exception as e: print(f"⚠️ avisar_nuevos: {e}")

    # 7) Guardar estado. Se relee la DB para no pisar lo que cambió mientras corría el ciclo (ej. /apagar).
    db2 = leer_db()
    db2["productos_proveedor"] = prov_nuevo if completo else prov_consolidado
    db2["categorias_conocidas"] = cats_guardar
    db2["enviado_web"] = enviado
    if reconciliar and lote_envio and web_ok and not resumen.get("lotes_fallidos"):
        db2["reconciliado_at"] = time.time()
    if completo:
        db2["ultimo_completo_n"] = len(prov_nuevo)
    if caidos:
        db2.pop("forzar_caidos", None)
    if ofertas_nuevas:
        db2["ofertas_pendientes"] = ofertas_nuevas
    escribir_db(db2)

    _sumar_dia(cambios_stock=resumen.get("cambios_stock", 0) or 0, cambios_precio=resumen.get("cambios_precio", 0) or 0,
               subas=n_subas, bajas=n_bajas, subas_fuertes=len(subas_fuertes), nuevos=resumen.get("insertados", 0) or 0,
               sin_stock=len(resumen.get("sin_stock", [])) + afectados, con_stock=len(resumen.get("con_stock", [])),
               ofertas=len(ofertas_nuevas), fotos=n_fotos,
               sin_stock_vendidos=[x.get("nombre") for x in sin_stock_vend][:20])
    _registrar_ciclo(inicio, True, "" if completo else "lectura incompleta del proveedor", productos=len(prov_nuevo), referencia=n_ref,
                     enviados=len(lote_envio), cambios=n_cambios, nuevos=resumen.get("insertados", 0) or 0)
    print("─── ✅ Ciclo completado ───")

# ══════════════════════════════════════════════════════════════════════════════
# TAREAS PERIÓDICAS: créditos, resumen diario, recordatorio de scraping apagado
# ══════════════════════════════════════════════════════════════════════════════
CREDITOS_CADA_H = 6

def chequear_creditos():
    """Créditos de los servicios pagos. Avisa antes de que se terminen (Cloudinary 75% y 90%, ScraperAPI 90%)."""
    s = salud_web()
    cl = (s or {}).get("cloudinary") or {}
    if cl.get("error"):
        alerta("cloudinary_error", "No pude consultar el crédito de Cloudinary", _md(cl["error"]), grave=False)
    elif cl.get("limite"):
        resolver("cloudinary_error", avisar=False)
        pct = float(cl.get("pct") or 0)
        txt = f"Va {pct:.0f}% del plan del mes ({cl.get('usado', 0):.1f} de {cl.get('limite', 0):.0f} créditos)."
        if pct >= 90:
            resolver("cloudinary_medio", avisar=False)
            alerta("cloudinary_alto", f"Cloudinary casi sin crédito ({pct:.0f}%)",
                   txt + " Si se termina, las fotos nuevas no se suben y las de la web pueden dejar de verse.")
        elif pct >= 75:
            resolver("cloudinary_alto", avisar=False)
            alerta("cloudinary_medio", f"Cloudinary al {pct:.0f}% del plan", txt, grave=False)
        else:
            resolver("cloudinary_alto"); resolver("cloudinary_medio")
    sc = creditos_scraperapi()
    for k in sc:
        clave = f"scraperapi_{k['key']}"
        if k.get("pct") is not None and k["pct"] >= 90:
            alerta(clave, f"ScraperAPI (clave {k['key']}) casi sin créditos",
                   f"Usó {k['usado']} de {k['limite']} pedidos del mes. Es el último respaldo para leer el proveedor.", grave=False)
        elif k.get("pct") is not None:
            resolver(clave)
    _mod_estado(lambda e: e.update({"creditos_at": time.time(), "creditos": {"cloudinary": cl, "scraperapi": sc}}))
    return cl, sc

def _linea_creditos(cl):
    if not cl: return "Cloudinary: sin datos"
    if cl.get("error"): return f"Cloudinary: no se pudo consultar ({_md(cl['error'])})"
    if not cl.get("limite"): return "Cloudinary: sin datos"
    return f"Cloudinary: {float(cl.get('pct') or 0):.0f}% del plan del mes"

def _lineas_alertas(est):
    al = est.get("alertas", {})
    if not al: return "Nada pendiente de revisar."
    return "\n".join(f"{'🚨' if a.get('grave') else '⚠️'} {a.get('titulo')} (desde las {_hora_ar(a.get('desde', time.time()))})"
                     for a in sorted(al.values(), key=lambda a: a.get("desde", 0)))

def resumen_diario():
    """A las RESUMEN_HORA (hora AR) manda lo que pasó desde el resumen anterior. Sirve también de 'el bot está vivo'."""
    est = leer_estado()
    hoy = _ahora_ar().strftime("%Y-%m-%d")
    if not est.get("resumen_fecha"):
        # Recién arrancado (deploy): el próximo resumen sale mañana con datos de verdad
        _mod_estado(lambda e: e.update({"resumen_fecha": hoy if _ahora_ar().hour >= RESUMEN_HORA else (_ahora_ar() - timedelta(days=1)).strftime("%Y-%m-%d")}))
        return
    if _ahora_ar().hour < RESUMEN_HORA or est.get("resumen_fecha") == hoy:
        return
    dia = est.get("dia", {})
    pend = comerciapp_pendientes()
    cl, _sc = chequear_creditos()
    est = leer_estado()
    activo = leer_db().get("scraping_activo", False)
    uc = est.get("ultimo_ciclo", {})
    sv = [n for n in (dia.get("sin_stock_vendidos") or []) if n]
    lineas = [
        f"• Ciclos: {dia.get('ciclos_ok', 0)} bien" + (f", {dia['ciclos_fallidos']} con problemas" if dia.get("ciclos_fallidos") else ""),
        f"• Cambios en la web: {dia.get('cambios_stock', 0)} de stock, {dia.get('cambios_precio', 0)} de precio",
        f"• Precios del proveedor: {_pl(dia.get('subas', 0), 'suba', 'subas')}" + (f" ({_pl(dia['subas_fuertes'], 'fuerte', 'fuertes')})" if dia.get("subas_fuertes") else "") + f", {_pl(dia.get('bajas', 0), 'baja', 'bajas')}",
        f"• Productos nuevos: {dia.get('nuevos', 0)}" + (f" · {len(pend)} esperan tu aprobación (/pendientes)" if pend else ""),
        f"• Se quedaron sin stock: {dia.get('sin_stock', 0)}" + (f" — de los que vendés: {', '.join(_md(n) for n in sv[:8])}" if sv else ""),
        f"• Volvieron a tener stock: {dia.get('con_stock', 0)}",
    ]
    if dia.get("ofertas"): lineas.append(f"• Ofertas nuevas del proveedor: {dia['ofertas']}")
    lineas.append(f"• Web Unlocker: {dia.get('pedidos', 0)} pedidos · {texto_consumo()}")
    if dia.get("fotos"): lineas.append(f"• Fotos subidas a la web: {dia['fotos']}")
    estado = (f"Scraping {'encendido' if activo else 'APAGADO (mandá /encender)'}"
              + (f" · último ciclo {_hora_ar(uc['at'])} {('incompleto' if uc.get('motivo') else 'bien') if uc.get('ok') else 'con problemas'}" if uc.get("at") else ""))
    tg(f"☀️ *{_nt('Resumen del día')}*\n\n" + "\n".join(lineas) +
       f"\n\n*Estado*\n{estado}\n{_linea_creditos(cl)}\n\n*Para revisar*\n{_lineas_alertas(est)}", silencioso=False)
    _mod_estado(lambda e: e.update({"resumen_fecha": hoy, "dia": {}}))

def recordar_scraping_apagado():
    activo = leer_db().get("scraping_activo", False)
    if activo:
        _mod_estado(lambda e: e.pop("apagado_desde", None))
        resolver("apagado", avisar=False)
        return
    desde = _mod_estado(lambda e: e.setdefault("apagado_desde", time.time()))
    if time.time() - desde > APAGADO_AVISO_H * 3600:
        alerta("apagado", "El scraping está apagado",
               f"Hace {_dur(time.time() - desde)} que el bot no sincroniza con el proveedor (después de cada deploy arranca apagado). "
               "Mandá /encender. Si lo apagaste a propósito, ignorá este aviso.", grave=False)

def tareas_periodicas():
    for f in (recordar_scraping_apagado, resumen_diario):
        try: f()
        except Exception as e: print(f"⚠️ {f.__name__}: {e}")
    try:
        if time.time() - leer_estado().get("creditos_at", 0) > CREDITOS_CADA_H * 3600:
            chequear_creditos()
    except Exception as e: print(f"⚠️ créditos: {e}")

def texto_estado():
    est = leer_estado()
    db = leer_db()
    activo = db.get("scraping_activo", False)
    uc = est.get("ultimo_ciclo", {})
    cl, sc = chequear_creditos()
    est = leer_estado()
    lin = [f"*{_nt('Estado del bot')}*", "",
           f"• Scraping: {'▶️ encendido, cada ' + str(CICLO_MINUTOS) + ' min (de noche cada ' + str(CICLO_NOCHE_MIN) + ')' if activo else '⏸️ APAGADO (mandá /encender)'}"]
    if uc.get("at"):
        if not uc.get("ok"): res = "con problemas: " + _md(uc.get("motivo", ""))
        elif uc.get("motivo"): res = "⚠️ INCOMPLETO" + (f" ({uc.get('productos', 0)} de unos {uc['referencia']} productos)" if uc.get("referencia") else "") + ": no se marcó nada sin stock"
        else: res = "bien"
        lin.append(f"• Último ciclo: {_hora_ar(uc['at'])} (hace {_dur(time.time() - uc['at'])}) · {res} · "
                   f"tardó {_dur(uc.get('dur', 0)) if uc.get('dur', 0) >= 60 else str(uc.get('dur', 0)) + ' s'}")
        if uc.get("productos"):
            lin.append(f"• Proveedor: {uc['productos']} productos" + (f" vía {FUENTES.get(uc['fuente'], uc['fuente'])}" if uc.get("fuente") else "")
                       + (f" (lectura {uc['modo']})" if uc.get("modo") else "")
                       + f" · a la web: {uc.get('enviados', 0)} enviados, {uc.get('cambios', 0)} con cambios")
        lin.append(f"• Web Unlocker: {uc.get('pedidos', 0)} pedidos el último ciclo · {texto_consumo()}")
    else:
        lin.append("• Último ciclo: todavía no corrió desde que arrancó el bot")
    lin.append(f"• Web: {'✅ conectada' if comerciapp_ok() else '❌ falta `COMERCIAPP_API` / `BOT_API_KEY`'}")
    lin.append(f"• Proveedor en memoria: {len(db.get('productos_proveedor', {}))} productos")
    lin.append(f"• {_linea_creditos(cl)}")
    for k in sc:
        if k.get("pct") is not None: lin.append(f"• ScraperAPI clave {k['key']}: {k['pct']}% usado")
    lin.append(f"• Avisos sin sonido de {SILENCIO_DESDE:02d} a {SILENCIO_HASTA:02d} hs · resumen diario {RESUMEN_HORA:02d} hs")
    lin += ["", "*Para revisar*", _lineas_alertas(est)]
    return "\n".join(lin)

# ══════════════════════════════════════════════════════════════════════════════
# COMPRAS AUTOMÁTICAS AL PROVEEDOR
# ══════════════════════════════════════════════════════════════════════════════
def _prov_session():
    if not PROV_USER or not PROV_PASS:
        print("❌ Falta PROV_USER o PROV_PASS en Railway Variables")
        return None, None
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        r = session.get(PROV_LOGIN_URL, timeout=20)
        if r.status_code != 200:
            print(f"❌ Login page HTTP {r.status_code}"); return None, None
        login_data = {
            "log":         PROV_USER,
            "pwd":         PROV_PASS,
            "wp-submit":   "Acceder",
            "redirect_to": "https://rxzweb.com/mi-cuenta/",
            "testcookie":  "1"
        }
        r2 = session.post(PROV_LOGIN_URL, data=login_data, timeout=20)
        if "mi-cuenta" not in r2.url and "dashboard" not in r2.url:
            print(f"❌ Login falló. URL final: {r2.url[:80]}")
            return None, None
        print("✅ Login rxzweb exitoso")
        r3 = session.get(PROV_CART_URL, timeout=20)
        nonce = r3.headers.get("X-WC-Store-API-Nonce") or r3.headers.get("Nonce", "")
        if not nonce:
            r4 = session.get("https://rxzweb.com/", timeout=20)
            import re as _re
            m = _re.search(r'"nonce":"([^"]+)"', r4.text)
            if m: nonce = m.group(1)
        print(f"   Nonce: {nonce[:20] if nonce else 'NO ENCONTRADO'}...")
        return session, nonce
    except Exception as e:
        print(f"❌ Session: {e}"); return None, None

def _prov_add_to_cart(session, nonce, product_id, variation_id, quantity):
    headers = {"Nonce": nonce, "Content-Type": "application/json"}
    payload = {"id": variation_id or product_id, "quantity": quantity}
    try:
        r = session.post(f"{PROV_CART_URL}/add-item", json=payload, headers=headers, timeout=20)
        ok = r.status_code in (200, 201)
        print(f"  {'✅' if ok else '❌'} Carrito add {product_id}: HTTP {r.status_code}")
        if not ok: print(f"     {r.text[:100]}")
        return ok
    except Exception as e:
        print(f"❌ Add to cart: {e}"); return False

def _prov_checkout(session, nonce, datos_cliente):
    headers = {"Nonce": nonce, "Content-Type": "application/json"}
    payload = {
        "billing_address": {
            "first_name": datos_cliente.get("first_name", ""),
            "last_name":  datos_cliente.get("last_name", ""),
            "company":    CUIT_PROVEEDOR or "",
            "address_1":  datos_cliente.get("address_1", ""),
            "city":       datos_cliente.get("city", ""),
            "state":      datos_cliente.get("state", "B"),
            "postcode":   datos_cliente.get("postcode", ""),
            "country":    "AR",
            "email":      datos_cliente.get("email", PROV_USER or ""),
            "phone":      datos_cliente.get("phone", ""),
        },
        "shipping_address": {
            "first_name": datos_cliente.get("first_name", ""),
            "last_name":  datos_cliente.get("last_name", ""),
            "company":    "",
            "address_1":  datos_cliente.get("address_1", ""),
            "city":       datos_cliente.get("city", ""),
            "state":      datos_cliente.get("state", "B"),
            "postcode":   datos_cliente.get("postcode", ""),
            "country":    "AR",
            "phone":      datos_cliente.get("phone", ""),
        },
        "payment_method": "cod",
        "customer_note":  datos_cliente.get("nota", ""),
    }
    try:
        r = session.post(PROV_CHKOUT_URL, json=payload, headers=headers, timeout=30)
        if r.status_code in (200, 201):
            data = r.json()
            orden = data.get("order_id") or data.get("id") or data.get("number")
            print(f"✅ Pedido creado en proveedor: #{orden}")
            return str(orden)
        else:
            print(f"❌ Checkout HTTP {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        print(f"❌ Checkout: {e}"); return None

def hacer_pedido_proveedor(items, datos_cliente, nota=""):
    print("🛒 Iniciando compra automática al proveedor...")
    session, nonce = _prov_session()
    if not session:
        tg("❌ No pude iniciar sesión en el proveedor. Verificá PROV_USER y PROV_PASS.")
        return None
    for item in items:
        ok = _prov_add_to_cart(session, nonce, item["product_id"], item.get("variation_id"), item["quantity"])
        if not ok:
            tg(f"❌ No pude agregar al carrito: {item.get('nombre', item['product_id'])}")
            return None
        time.sleep(0.5)
    if nota:
        datos_cliente["nota"] = nota
    orden = _prov_checkout(session, nonce, datos_cliente)
    return orden

def procesar_orden_pagada(datos_orden):
    num_orden  = str(datos_orden.get("id") or datos_orden.get("number","?"))
    cliente    = datos_orden.get("billing", {})
    productos  = datos_orden.get("products", datos_orden.get("line_items", []))
    envio_tipo = datos_orden.get("shipping", {}).get("pickup_type", "shipping")
    es_retiro  = "pickup" in str(envio_tipo).lower() or "retiro" in str(envio_tipo).lower()
    db      = leer_db()
    prov    = db.get("productos_proveedor", {})
    idx     = construir_indice(prov)
    items_pedido = []
    hay_problema = False
    lineas_msg   = ["Orden pagada #" + num_orden, ""]
    for item in productos:
        nombre   = item.get("name", item.get("product_name","?"))
        cantidad = item.get("quantity", 1)
        nn       = normalizar(nombre)
        _, dp    = buscar_en_indice(nn, idx)
        if dp is None:
            lineas_msg.append("NO ENCONTRADO: " + nombre + " x" + str(cantidad))
            hay_problema = True
            continue
        stock_disp = dp.get("stock_base", 0)
        if stock_disp < cantidad:
            lineas_msg.append("STOCK BAJO: " + nombre + " x" + str(cantidad) + " (disponible: " + str(stock_disp) + ")")
            hay_problema = True
        else:
            lineas_msg.append("OK: " + nombre + " x" + str(cantidad))
        woo_id = None; var_id = None
        for clave, d in prov.items():
            base = normalizar(d.get("nombre_base_proveedor", clave))
            if match(nn, base):
                woo_id = d.get("woo_id")
                if "(" in d.get("nombre_real",""):
                    var_id = woo_id
                    for c2,d2 in prov.items():
                        if normalizar(d2.get("nombre_base_proveedor",""))==base and "(" not in d2.get("nombre_real",""):
                            woo_id=d2.get("woo_id"); break
                break
        items_pedido.append({"nombre":nombre,"product_id":woo_id,"variation_id":var_id,"quantity":cantidad})
    ship = datos_orden.get("shipping", {})
    datos_cli = {
        "first_name": cliente.get("first_name",""),
        "last_name":  cliente.get("last_name",""),
        "address_1":  ship.get("address", cliente.get("address","")),
        "city":       ship.get("city", cliente.get("city","")),
        "state":      ship.get("province","B"),
        "postcode":   ship.get("zipcode",""),
        "phone":      cliente.get("phone",""),
        "email":      cliente.get("email",""),
    }
    nota = "Orden cliente #" + num_orden + (" - RETIRO EN LOCAL" if es_retiro else "")
    sep = chr(10)
    if hay_problema:
        lineas_msg.append("")
        lineas_msg.append("Hay problemas de stock.")
        lineas_msg.append("Usa /confirmar_pedido " + num_orden + " para proceder igual.")
        tg("[" + NOMBRE_TIENDA + "] " + sep.join(lineas_msg))
        db["pedido_pendiente"] = {"num_orden":num_orden,"items":items_pedido,"cliente":datos_cli,"nota":nota}
        escribir_db(db)
    else:
        tg("[" + NOMBRE_TIENDA + "] " + sep.join(lineas_msg))
        orden_prov = hacer_pedido_proveedor(items_pedido, datos_cli, nota)
        if orden_prov:
            tg("[" + NOMBRE_TIENDA + "] Pedido enviado al proveedor. Cliente #" + num_orden + " -> Proveedor #" + orden_prov)
            db.setdefault("ordenes",{})[num_orden] = {"orden_prov": orden_prov}
            escribir_db(db)
        else:
            tg("[" + NOMBRE_TIENDA + "] ERROR: No pude hacer el pedido #" + num_orden + ". Hacelo manualmente.")

# ══════════════════════════════════════════════════════════════════════════════
# COMANDOS TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════
def run_productos_sin_cargar(modo_todo=False):
    if not _token:
        tg("❌ Necesito el token primero."); return
    tg(f"🔍 *{_nt('Buscando productos del proveedor sin cargar...')}*")
    productos_prov = {}; pagina = 1
    while True:
        r = _prov_get(PROV_API, params={"per_page":100,"page":pagina})
        if not r or r.status_code not in (200,201,202): break
        lote = r.json()
        if not lote: break
        for p in lote:
            nombre = p.get("name","").strip()
            if not nombre or len(nombre) < 4: continue
            cats = [c.get("slug","").lower() + " " + c.get("name","").lower()
                    for c in p.get("categories",[])]
            cats_str = " ".join(cats)
            if any(exc in cats_str for exc in CATEGORIAS_EXCLUIDAS):
                continue
            precio = int(p.get("prices",{}).get("price",0)) // 100
            if precio == 0: continue
            stock = p.get("add_to_cart",{}).get("maximum") or 0
            productos_prov[normalizar(nombre)] = {
                "nombre_real": nombre,
                "precio": precio,
                "precio_sug": precio_obj(precio),
                "stock": stock,
            }
        if len(lote) < 100: break
        pagina += 1; time.sleep(0.5)
    catalogo = obtener_catalogo()
    cat_norms = {p["nombre_norm"] for p in catalogo}
    sin_cargar = []
    for norm, datos in productos_prov.items():
        en_tienda = any(match(norm, cn) for cn in cat_norms)
        if en_tienda: continue
        if not modo_todo:
            if not any(w in norm for w in PALABRAS_INTERES): continue
        sin_cargar.append(datos)
    if not sin_cargar:
        modo_txt = "en toda la lista" if modo_todo else "con palabras de interés"
        tg(f"ℹ️ No hay productos sin cargar {modo_txt}."); return
    modo_txt = "🗂 *Todos* (sin Pantallas/Baterías)" if modo_todo else "🎯 *Filtrado por palabras de interés*"
    header = f"📦 *{_nt('Productos sin cargar en tu tienda')}*\n{modo_txt}\nTotal: *{len(sin_cargar)}*"
    tg(header)
    lineas = []
    for d in sin_cargar:
        stock_txt = f"Stock: {d['stock']}" if d['stock'] < 9999 else "Stock: ∞"
        linea = f"• *{d['nombre_real']}*\n  Costo: ${d['precio']:,} → Sugerido: ${d['precio_sug']:,} | {stock_txt}"
        lineas.append(linea)
    for i in range(0, len(lineas), 20):
        tg("\n\n".join(lineas[i:i+20]))

# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK SERVER
# ══════════════════════════════════════════════════════════════════════════════
if FLASK_OK:
    flask_app = Flask(__name__)

    @flask_app.route("/webhook", methods=["POST"])
    def recibir_webhook():
        try:
            data = request.get_json(silent=True) or {}
            evento   = data.get("event", "")
            orden_id = data.get("id")
            print(f"📨 Webhook: {evento} id={orden_id}")
            if orden_id:
                if evento == "order/created":
                    threading.Thread(target=procesar_webhook_pedido_nuevo, args=(orden_id,), daemon=True).start()
                elif evento == "order/paid":
                    threading.Thread(target=procesar_webhook_pedido_pagado, args=(orden_id,), daemon=True).start()
        except Exception as e:
            print(f"❌ Webhook error: {e}")
        return jsonify({"status": "ok"}), 200

    @flask_app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "tienda": NOMBRE_TIENDA}), 200

    def run_flask():
        port = int(os.environ.get("PORT", 8080))
        print(f"🌐 Webhook server en puerto {port}")
        flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def obtener_orden(orden_id):
    r = _get(f"{API_BASE}/orders/{orden_id}")
    if r and r.status_code == 200:
        return r.json()
    print(f"❌ No pude obtener orden {orden_id}: HTTP {r.status_code if r is not None else 'None'}")
    return None

def procesar_webhook_pedido_nuevo(orden_id):
    time.sleep(1)
    orden = obtener_orden(orden_id)
    if not orden:
        tg("⚠️ *" + _nt('Nuevo pedido') + f"* recibido pero no pude obtener detalles (ID: {orden_id})")
        return
    num      = str(orden.get("number", orden_id))
    productos = orden.get("products", [])
    total    = orden.get("total", "0")
    pago_st  = orden.get("payment_status", "?")
    envio    = orden.get("shipping", {})
    metodo   = envio.get("pickup_type", "") or envio.get("method", "")
    db   = leer_db()
    prov = db.get("productos_proveedor", {})
    msg = "🛒 *" + _nt('¡Nuevo Pedido #' + num + '!') + "*\n\n"
    msg += f"💰 Total: *${float(total or 0):,.0f}*\n"
    msg += f"💳 Pago: *{pago_st}*\n"
    if metodo:
        msg += f"📦 Envío: *{metodo}*\n"
    msg += "\n"
    if prov:
        idx = construir_indice(prov)
        for item in productos:
            nombre   = item.get("name", "?")
            cantidad = item.get("quantity", 1)
            nn       = normalizar(nombre)
            _, dp    = buscar_en_indice(nn, idx)
            if dp:
                stock = dp.get("stock_base", 0)
                icono = "⚠️" if stock < cantidad else "✅"
                stock_txt = f" (stock: {stock})" if stock < cantidad else ""
                msg += f"{icono} *{nombre}* x{cantidad}{stock_txt}\n"
            else:
                msg += f"❓ *{nombre}* x{cantidad}\n"
    else:
        for item in productos:
            msg += f"• *{item.get('name','?')}* x{item.get('quantity',1)}\n"
    tg(msg)

def procesar_webhook_pedido_pagado(orden_id):
    time.sleep(1)
    orden = obtener_orden(orden_id)
    if not orden:
        tg("⚠️ *" + _nt('Pedido pagado') + f"* pero no pude obtener detalles (ID: {orden_id})")
        return
    num = str(orden.get("number", orden_id))
    tg("💚 *" + _nt('¡Pago confirmado! Pedido #' + num) + "*")
    procesar_orden_pagada(orden)

# ══════════════════════════════════════════════════════════════════════════════
# GENERACIÓN DE DESCRIPCIONES CON IA
# ══════════════════════════════════════════════════════════════════════════════
def buscar_specs_producto(nombre):
    try:
        query = nombre.replace(" ", "+") + "+especificaciones+ficha+tecnica"
        r = requests.get(
            f"https://duckduckgo.com/html/?q={query}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10
        )
        import re as _re
        snippets = _re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', r.text)
        limpios = []
        for s in snippets[:5]:
            limpio = _re.sub(r'<[^>]+>', '', s).strip()
            if limpio and len(limpio) > 30:
                limpios.append(limpio)
        return " | ".join(limpios[:3]) if limpios else ""
    except Exception as e:
        print(f"⚠️ Búsqueda web specs: {e}")
        return ""

def generar_descripcion_ia(nombre, precio):
    if not GROQ_API_KEY:
        return None, "Falta GROQ_API_KEY en Railway Variables"
    specs = buscar_specs_producto(nombre)
    contexto_specs = f"\nInformacion encontrada en la web sobre el producto:\n{specs}\n" if specs else ""
    prompt = (
        "Sos un experto en herramientas para tecnicos de reparacion electronica de celulares y placas en Argentina.\n\n"
        f"Genera una descripcion de producto para una tienda online orientada a tecnicos profesionales.\n"
        f"Producto: {nombre}\n"
        f"Precio: ${precio:,}\n"
        f"{contexto_specs}\n"
        "La descripcion debe:\n"
        "- Estar en espanol argentino, sin tuteo\n"
        "- Tener entre 150 y 250 palabras\n"
        "- Explicar para que sirve y que problemas resuelve\n"
        "- Mencionar compatibilidades, modelos o especificaciones tecnicas reales si las encontras\n"
        "- Usar lenguaje tecnico apropiado para profesionales\n"
        "- NO usar markdown ni asteriscos, solo texto plano con saltos de linea\n"
        "- Solo incluir especificaciones que esten respaldadas por la informacion encontrada\n\n"
        "Solo devuelve la descripcion, sin titulo ni comentarios adicionales."
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "max_tokens": 1000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip(), None
        return None, f"Groq API HTTP {r.status_code}: {r.text[:150]}"
    except Exception as e:
        return None, f"Error Groq API: {e}"

def set_descripcion_producto(pid, descripcion):
    r = _put(f"{API_BASE}/products/{pid}", {"description": {"es": descripcion}})
    return r is not None and r.status_code in (200, 201)

def run_generar_todas_descripciones():
    catalogo = obtener_catalogo(forzar=True)
    if not catalogo:
        tg("No pude obtener el catalogo."); return
    sin_desc = [p for p in catalogo if not p.get("descripcion")]
    total = len(sin_desc)
    if not sin_desc:
        tg("Todos los productos ya tienen descripcion."); return
    tg(f"*{_nt('Generando descripciones')}*\n\n"
       f"Productos sin descripcion: *{total}*\n"
       f"Tiempo estimado: ~{total * 3 // 60} minutos\n\n"
       f"Te aviso cada 10 productos.")
    ok = 0; errores = []; i = 0
    for prod in sin_desc:
        i += 1
        desc, err = generar_descripcion_ia(prod["nombre"], int(prod["precio_base"]))
        if err:
            errores.append(prod["nombre"])
            print(f"Error IA {prod['nombre']}: {err}")
        elif set_descripcion_producto(prod["id"], desc):
            ok += 1
            print(f"OK desc: {prod['nombre']}")
        else:
            errores.append(prod["nombre"])
        if i % 10 == 0:
            tg(f"Progreso: *{i}/{total}* ({ok} OK, {len(errores)} errores)")
        time.sleep(2)
    tg(f"*{_nt('Descripciones completadas')}*\n\n"
       f"Exitosas: *{ok}*\n"
       f"Errores: *{len(errores)}*" +
       (f"\n\nFallaron:\n" + "\n".join(f"• {e}" for e in errores[:10]) if errores else ""))

def run_ver_ofertas_proveedor():
    db   = leer_db()
    prov = db.get("productos_proveedor", {})
    if not prov:
        tg("Sin datos del proveedor. Esperá un ciclo primero."); return
    ofertas = {}
    for clave, d in prov.items():
        if not d.get("en_oferta"): continue
        precio     = d.get("precio", 0)
        precio_ant = d.get("precio_anterior", 0)
        if not precio or not precio_ant or precio >= precio_ant: continue
        base = d.get("nombre_base_proveedor", clave)
        if base not in ofertas:
            ofertas[base] = {
                "nombre_real":    d.get("nombre_real", base),
                "precio":         precio,
                "precio_anterior": precio_ant,
                "precio_web":     precio_obj(precio),
                "precio_web_ant": precio_obj(precio_ant),
                "descuento":      round((1 - precio / precio_ant) * 100),
            }
    if not ofertas:
        tg("No hay ofertas activas del proveedor en este momento."); return
    lista = list(ofertas.items())
    lineas = []
    for i, (base, d) in enumerate(lista, 1):
        ahorro = d["precio_web_ant"] - d["precio_web"]
        lineas.append(
            f"*{i}.* {d['nombre_real']}\n"
            f"   Costo: ~~${d['precio_anterior']:,}~~ → ${d['precio']:,} (-{d['descuento']}%)\n"
            f"   Tu precio: ~~${d['precio_web_ant']:,}~~ → *${d['precio_web']:,}* (ahorrás ${ahorro:,})"
        )
    db["ofertas_pendientes"] = {base: {
        "nombre_real":    d["nombre_real"],
        "precio":         d["precio"],
        "precio_anterior": d["precio_anterior"],
        "en_oferta":      True,
        "stock":          prov.get(base, {}).get("stock", 0),
        "nombre_base_proveedor": base,
    } for base, d in lista}
    escribir_db(db)
    encabezado = (f"*{_nt('Ofertas activas del proveedor')}*\n"
                  f"Total: *{len(lista)}*\n\n"
                  f"Usá `/aplicar_ofertas todos` o `/aplicar_ofertas 1 3 5` para aplicar.")
    tg(encabezado)
    for i in range(0, len(lineas), 15):
        tg("\n\n".join(lineas[i:i+15]))

def set_video_producto(pid, url):
    r = _put(f"{API_BASE}/products/{pid}", {"video_url": url})
    return r is not None and r.status_code in (200, 201)

def tg_menu():
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    keyboard = {
        "inline_keyboard": [
            [{"text": "▶️ Encender scraping", "callback_data": "/encender"},
             {"text": "⏸️ Apagar scraping", "callback_data": "/apagar"}],
            [{"text": "🔁 Ciclo Manual", "callback_data": "/ciclo"},
             {"text": "📊 Estado", "callback_data": "/estado"}],
            [{"text": "🔬 Test Proxy", "callback_data": "/test_proxy"}],
            [{"text": "📂 Ver Categorías", "callback_data": "/ver_categorias"}],
            [{"text": "🆕 Productos nuevos por aprobar", "callback_data": "/pendientes"}],
            [{"text": "🖼️ Reparar fotos de la web", "callback_data": "/reparar_fotos"}],
            [{"text": "📸 Migrar Fotos Empretienda", "callback_data": "/migrar_fotos"}],
            [{"text": "🧹 Limpiar DEPOSITO", "callback_data": "/limpiar_deposito"}],
            [{"text": "🏷️ Ver Ofertas Proveedor", "callback_data": "/ver_ofertas_proveedor"}],
            [{"text": "🛠️ Debug Env", "callback_data": "/debug_env"}],
            [{"text": "📄 Ayuda completa", "callback_data": "/ayuda"}],
        ]
    }
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID,
                  "text": f"*{_nt('Panel de control')}*\nElegí una opción:",
                  "parse_mode": "Markdown",
                  "reply_markup": keyboard},
            timeout=15
        )
    except Exception as e:
        print(f"❌ Menu: {e}")

def run_buscar_todos_videos():
    catalogo = obtener_catalogo(forzar=True)
    if not catalogo:
        tg("No pude obtener el catálogo."); return
    sin_video = []
    for prod in catalogo:
        r = _get(f"{API_BASE}/products/{prod['id']}")
        if r is not None and r.status_code == 200:
            if not r.json().get("video_url"):
                sin_video.append(prod)
        time.sleep(0.3)
    if not sin_video:
        tg("Todos los productos ya tienen video asignado."); return
    tg(f"Buscando videos para *{len(sin_video)}* productos sin video...")
    ok = 0; sin_resultado = []
    for prod in sin_video:
        nombre = prod["nombre"]
        query = nombre.replace(" ", "+") + "+herramienta+reparacion"
        try:
            r_yt = requests.get(
                f"https://www.youtube.com/results?search_query={query}",
                headers={"User-Agent": USER_AGENT}, timeout=10
            )
            import re as _re
            matches = _re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', r_yt.text)
            if matches:
                video_url = f"https://www.youtube.com/watch?v={matches[0]}"
                if set_video_producto(prod["id"], video_url):
                    ok += 1
                    print(f"Video OK: {nombre} -> {video_url}")
                else:
                    sin_resultado.append(nombre)
            else:
                sin_resultado.append(nombre)
        except Exception as e:
            print(f"Error buscando video para {nombre}: {e}")
            sin_resultado.append(nombre)
        time.sleep(1)
    tg(f"*Videos asignados:* {ok}\n*Sin resultado:* {len(sin_resultado)}")
    if sin_resultado:
        tg("Sin video:\n" + "\n".join(f"• {n}" for n in sin_resultado[:20]))

def _scrapear_empretienda_fotos():
    """Recorre la tienda vieja de Empretienda vía su API interna /v4/product y devuelve
    [{nombre, imagenes:[url]}] SOLO de productos en stock. Paginación con filter_page."""
    from bs4 import BeautifulSoup
    import re as _re
    TIENDA = (os.environ.get("EMPRETIENDA_URL") or "https://leandroidgremio.empretienda.com.ar").rstrip("/")

    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})

    # 1) Cargar la página de productos para obtener cookies de sesión + CSRF token
    csrf = ""
    try:
        r0 = sess.get(f"{TIENDA}/productos", timeout=30)
        m = _re.search(r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', r0.text)
        if not m:
            m = _re.search(r'csrf[_-]?token["\']?\s*[:=]\s*["\']([^"\']+)["\']', r0.text, _re.I)
        if m: csrf = m.group(1)
    except Exception as e:
        print(f"   Empretienda: no pude obtener CSRF ({e})")

    api_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"{TIENDA}/productos",
    }
    if csrf:
        api_headers["X-CSRF-TOKEN"] = csrf
        print(f"   Empretienda: CSRF token obtenido ✓")

    def _get(url):
        for _ in range(3):
            try:
                r = sess.get(url, headers=api_headers, timeout=30)
                if r.status_code == 200:
                    return r
            except Exception: pass
            time.sleep(2)
        return None

    def _extraer_de_json(data):
        """Empretienda /v4/product devuelve {status, data:[...], message}.
        Cada producto trae imagenes[] con i_link → URL en CloudFront. Sin ir al detalle."""
        CDN = "https://d22fxaf9t8d39k.cloudfront.net/"
        out = []
        prods = None
        if isinstance(data, dict):
            v = data.get("data")
            if isinstance(v, list): prods = v
            elif isinstance(v, dict):
                for k2 in ("products", "productos", "items", "data"):
                    if isinstance(v.get(k2), list): prods = v[k2]; break
        elif isinstance(data, list):
            prods = data
        if not prods: return out
        for p in prods:
            if not isinstance(p, dict): continue
            nombre = str(p.get("p_nombre") or p.get("nombre") or p.get("name") or "").strip()
            link = str(p.get("p_link") or p.get("link") or p.get("slug") or "").strip()
            # Imágenes: vienen en el listado como imagenes[].i_link
            lista_imgs = []
            for im in (p.get("imagenes") or []):
                il = im.get("i_link") if isinstance(im, dict) else None
                if il:
                    lista_imgs.append(il if il.startswith("http") else CDN + il)
            # Stock real: sumar stock[].s_cantidad (o ilimitado). Fallback a p_datos_stock.
            desactivado = p.get("p_desactivado")
            en_stock = True
            if desactivado in (1, "1", True, "true"): en_stock = False
            else:
                stock_arr = p.get("stock") or []
                if isinstance(stock_arr, list) and stock_arr:
                    total_cant = 0; ilimitado = False
                    for st in stock_arr:
                        if not isinstance(st, dict): continue
                        if st.get("s_ilimitado") in (1, "1", True): ilimitado = True
                        try: total_cant += int(st.get("s_cantidad") or 0)
                        except Exception: pass
                    en_stock = ilimitado or total_cant > 0
                else:
                    stock_raw = p.get("p_datos_stock", 1)
                    if isinstance(stock_raw, (int, float)): en_stock = stock_raw > 0
            if nombre:
                out.append({"nombre": nombre, "link": link, "en_stock": en_stock,
                            "imagen": lista_imgs[0] if lista_imgs else "", "imagenes": lista_imgs})
        return out

    def _extraer_de_html(html):
        soup = BeautifulSoup(html, "lxml")
        out = []
        for img in soup.find_all("img", alt=_re.compile(r"^Producto\s*-", _re.I)):
            alt = img.get("alt", "")
            nombre = _re.sub(r"^Producto\s*-\s*", "", alt).strip()
            src = img.get("src") or img.get("data-src") or ""
            if not nombre or not src: continue
            cont = img; sin_stock = False
            for _ in range(4):
                cont = cont.parent
                if cont is None: break
                if "SIN STOCK" in cont.get_text(" ", strip=True).upper():
                    sin_stock = True; break
            out.append({"nombre": nombre, "imagen": src, "en_stock": not sin_stock})
        return out

    vistos = {}
    pagina = 1
    debug_mostrado = False
    while pagina <= 100:
        url = f"{TIENDA}/v4/product?filter_page={pagina}&filter_order=0"
        r = _get(url)
        if not r: break
        prods = []
        raw = None
        try:
            raw = r.json()
        except Exception:
            prods = _extraer_de_html(r.text)
        if raw is not None:
            # DEBUG: en la primera página, mostrar la estructura para ajustar si hace falta
            if not debug_mostrado:
                debug_mostrado = True
                try:
                    if isinstance(raw, dict):
                        print(f"   [debug] claves del JSON: {list(raw.keys())[:10]}")
                        # buscar la primera lista de dicts para ver sus claves
                        for k, v in raw.items():
                            if isinstance(v, list) and v and isinstance(v[0], dict):
                                print(f"   [debug] '{k}'[0] claves: {list(v[0].keys())[:20]}")
                                break
                            if isinstance(v, dict):
                                for k2, v2 in v.items():
                                    if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                                        print(f"   [debug] '{k}.{k2}'[0] claves: {list(v2[0].keys())[:20]}")
                                        break
                    elif isinstance(raw, list) and raw and isinstance(raw[0], dict):
                        print(f"   [debug] item[0] claves: {list(raw[0].keys())[:20]}")
                except Exception: pass
            prods = _extraer_de_json(raw)
        if not prods: break
        nuevos = 0
        for p in prods:
            if p["nombre"] not in vistos:
                vistos[p["nombre"]] = p; nuevos += 1
        print(f"   Empretienda /v4/product pág {pagina}: {len(prods)} ({nuevos} nuevos)")
        if nuevos == 0: break
        pagina += 1; time.sleep(0.4)

    # 2) Las imágenes ya vienen en el listado (imagenes[].i_link) — sin ir al detalle
    fotos = []
    for n, d in vistos.items():
        if not d["en_stock"]:
            continue
        imgs = d.get("imagenes") or ([d["imagen"]] if d.get("imagen") else [])
        if imgs:
            fotos.append({"nombre": n, "imagenes": imgs})

    print(f"   Empretienda: {len(vistos)} únicos, {len(fotos)} en stock con foto")
    return fotos

def procesar_cmd(texto):
    global _token, _store_id
    texto = texto.strip()
    if "?code=" in texto:
        code = texto.split("?code=")[1].split("&")[0].strip()
        tg("🔄 Canjeando código OAuth...")
        token = canjear_code(code)
        if token:
            tg(f"✅ *¡Token obtenido!*\n\nGuardá en Railway → Variables:\n"
               f"• `API_TOKEN` = `{token}`\n• `API_USER_ID` = `{_store_id}`")
        else:
            tg("❌ Token fallido. El código dura 1 minuto.")
        return
    cmd = texto.lower().split()
    if not cmd: return

    if cmd[0] == "/encender":
        db = leer_db(); db["scraping_activo"] = True; escribir_db(db)
        recordar_scraping_apagado()
        tg(f"▶️ *Scraping ENCENDIDO* — el bot va a sincronizar cada {CICLO_MINUTOS} min.\n\nCorriendo un ciclo ahora...")
        def _encender():
            try:
                if ciclo_monitoreo() is False: tg("ℹ️ Ya había un ciclo corriendo: sigue ese.")
            except Exception as e: tg(f"⚠️ Error en el ciclo: `{e}`")
        threading.Thread(target=_encender, daemon=True).start()   # no frena los botones mientras corre
        return
    elif cmd[0] in ("/menu", "/start"):
        tg_menu()
        return
    elif cmd[0] in ("/estado", "/estado_scraping"):
        tg(texto_estado(), silencioso=True)
        return
    elif cmd[0] == "/forzar_caidos":
        db = leer_db(); db["forzar_caidos"] = True; escribir_db(db)
        tg("✅ En el próximo ciclo se marcan sin stock los productos que ya no están en el proveedor, aunque sean muchos.")
        return
    elif cmd[0] == "/apagar":
        db = leer_db(); db["scraping_activo"] = False; escribir_db(db)
        tg("⏸️ *Scraping APAGADO* — no va a salir al proveedor hasta que lo enciendas con /encender.\n\nUsalo antes de deployar cambios del bot para no bloquear RXZ.")
        return
    elif cmd[0] == "/ver_categorias":
        db = leer_db()
        conocidas = db.get("categorias_conocidas", [])
        excluidas = db.get("categorias_excluidas", CATEGORIAS_EXCLUIDAS_DEFAULT)
        if not conocidas:
            tg("ℹ️ Todavía no hay categorías registradas. Corré /ciclo una vez y volvé a probar.")
            return
        lineas = []
        for c in conocidas:
            marca = "🚫" if _categoria_excluida(c, excluidas) else "✅"
            lineas.append(f"{marca} {c}")
        tg(f"📂 *Categorías del proveedor*\n\n" + "\n".join(lineas) +
           f"\n\n✅ = se cargan | 🚫 = excluidas\n\nUsá `/excluir_categoria nombre` o `/incluir_categoria nombre`.")
        return
    elif cmd[0] == "/excluir_categoria":
        if len(cmd) < 2:
            tg("Usá: `/excluir_categoria nombre` (ej: `/excluir_categoria baterias`)"); return
        nombre = " ".join(texto.split()[1:]).strip()
        db = leer_db()
        excl = db.get("categorias_excluidas", CATEGORIAS_EXCLUIDAS_DEFAULT)
        if nombre.lower() not in [e.lower() for e in excl]:
            excl.append(nombre)
            db["categorias_excluidas"] = excl
            escribir_db(db)
            tg(f"🚫 Categoría *{nombre}* agregada a excluidas. No se cargará en el próximo ciclo.\n\n(Los productos ya cargados de esa categoría no se borran solos — si querés, limpiá y recargá.)")
        else:
            tg(f"ℹ️ *{nombre}* ya estaba excluida.")
        return
    elif cmd[0] == "/incluir_categoria":
        if len(cmd) < 2:
            tg("Usá: `/incluir_categoria nombre`"); return
        nombre = " ".join(texto.split()[1:]).strip()
        db = leer_db()
        excl = db.get("categorias_excluidas", CATEGORIAS_EXCLUIDAS_DEFAULT)
        nueva = [e for e in excl if e.lower() != nombre.lower() and nombre.lower() not in e.lower()]
        db["categorias_excluidas"] = nueva
        escribir_db(db)
        tg(f"✅ Categoría *{nombre}* incluida. Se cargará en el próximo /ciclo.")
        return
    elif cmd[0] == "/migrar_fotos" or cmd[0] == "/migrar_fotos_aplicar":
        aplicar = (cmd[0] == "/migrar_fotos_aplicar")
        if not comerciapp_ok():
            tg("❌ ComerciApp no configurado."); return
        tg(f"📸 *Migrando fotos de Empretienda* (modo {'APLICAR' if aplicar else 'REPORTAR'})...\n\nEsto recorre tu tienda vieja y tarda 1-2 min. Aguantá.")
        def _migrar():
            try:
                fotos = _scrapear_empretienda_fotos()
                if not fotos:
                    tg("❌ No encontré fotos en Empretienda. Revisá que la tienda esté online."); return
                r = requests.post(
                    f"{COMERCIAPP_API}/api/bot/fotos-por-nombre",
                    headers=_ca_headers(),
                    json={"fotos": fotos, "modo": "aplicar" if aplicar else "reportar"},
                    timeout=180)
                if r.status_code != 200:
                    tg(f"❌ Error HTTP {r.status_code}\n`{r.text[:200]}`"); return
                res = r.json()
                msg = (f"📊 *Resultado migración de fotos*\n\n"
                       f"• Productos en tu tienda: {res.get('total_productos')}\n"
                       f"• Fotos scrapeadas (en stock): {res.get('total_fotos')}\n"
                       f"• Matchearon por nombre: {res.get('matcheados')}\n"
                       f"   ↳ ya tenían foto: {res.get('ya_con_foto')}\n"
                       f"   ↳ sin foto (candidatos): {res.get('sin_foto_matcheados')}\n"
                       f"• No matchearon: {res.get('sin_match')}\n")
                if aplicar:
                    msg += f"\n✅ *Fotos aplicadas: {res.get('aplicados')}*"
                else:
                    msg += f"\n_(Modo reportar — no se aplicó nada.)_\nSi los candidatos se ven bien, mandá /migrar_fotos_aplicar"
                tg(msg)
                ej = res.get("ejemplos_sin_match")
                if ej:
                    tg("Ejemplos que NO matchearon:\n" + "\n".join(f"• {n}" for n in ej[:20]))
            except Exception as e:
                tg(f"❌ Error: `{e}`")
        threading.Thread(target=_migrar, daemon=True).start()
        return
    elif cmd[0] == "/pendientes":
        pend = comerciapp_pendientes()
        if pend is None: tg("❌ No pude consultar la web."); return
        if not pend: tg("✅ No hay productos nuevos esperando aprobación."); return
        tg(f"🆕 *{len(pend)} productos esperando aprobación*" + (" — te muestro los primeros 15" if len(pend) > 15 else ""))
        for p in pend[:15]: _ficha_nuevo(p)
        if len(pend) > 1:
            tg_botones("Todos los pendientes:", [[{"text": f"Publicar todos ({len(pend)})", "callback_data": "/pub_todos"},
                                          {"text": "Ignorar todos", "callback_data": "/ign_todos"}]])
        return
    elif cmd[0] in ("/pub", "/ign"):
        partes = texto.split()
        if len(partes) < 2: tg("Usá: `/pub RXZ-123` o `/ign RXZ-123`"); return
        sku = partes[1].strip().upper()
        publicar = cmd[0] == "/pub"
        r = comerciapp_aprobar([sku], publicar=publicar)
        if not r: tg("❌ No pude actualizar la web."); return
        if not r.get("afectados"): tg(f"ℹ️ `{sku}` no encontrado (¿ya lo aprobaste?)."); return
        nom = (r.get("productos") or [{}])[0].get("nombre", sku)
        tg(f"✅ *{_lim(nom)}* publicado en la web." if publicar else f"🙈 *{_lim(nom)}* queda oculto.")
        return
    elif cmd[0] in ("/pub_todos", "/ign_todos"):
        publicar = cmd[0] == "/pub_todos"
        r = comerciapp_aprobar(todos=True, publicar=publicar)
        if not r: tg("❌ No pude actualizar la web."); return
        tg(f"✅ {r.get('afectados', 0)} productos publicados." if publicar else f"🙈 {r.get('afectados', 0)} productos quedan ocultos.")
        return
    elif cmd[0] == "/aprobar_nuevos":
        db = leer_db()
        if len(cmd) > 1 and cmd[1] in ("on", "off", "si", "no"):
            db["aprobar_nuevos"] = cmd[1] in ("on", "si"); escribir_db(db)
        activo = db.get("aprobar_nuevos", True)
        tg(("✅ *Aprobación de nuevos: ACTIVADA*\nLo nuevo del proveedor entra oculto y te pregunto antes de publicarlo."
            if activo else "⏩ *Aprobación de nuevos: DESACTIVADA*\nLo nuevo del proveedor se publica solo.") +
           "\n\nCambialo con `/aprobar_nuevos on` u `/aprobar_nuevos off`.")
        return
    elif cmd[0] == "/reparar_fotos":
        threading.Thread(target=reparar_fotos, kwargs={"avisar": True}, daemon=True).start()
        return
    elif cmd[0] == "/limpiar_deposito":
        if not comerciapp_ok():
            tg("❌ ComerciApp no configurado."); return
        tg("🧹 *Limpiando DEPOSITO...* (borra TODOS los productos de esa sección para recargar sin duplicados)")
        def _limpiar():
            try:
                r = requests.post(f"{COMERCIAPP_API}/api/bot/limpiar-deposito", headers=_ca_headers(), json={}, timeout=60)
                if r.status_code == 200:
                    d = r.json()
                    tg(f"✅ *DEPOSITO limpio* — {d.get('borrados',0)} productos borrados.\n\nAhora mandá /ciclo para recargar los 416 productos con SKU correcto (sin duplicados y con todas las fotos).")
                else:
                    tg(f"❌ Error al limpiar: HTTP {r.status_code}\n`{r.text[:200]}`")
            except Exception as e:
                tg(f"❌ Error: `{e}`")
        threading.Thread(target=_limpiar, daemon=True).start()
        return
    elif cmd[0] == "/deduplicar":
        if not comerciapp_ok():
            tg("❌ ComerciApp no configurado."); return
        tg("🧹 *Deduplicando...* (deja 1 por producto, reengancha los pedidos al que queda)")
        def _dedup():
            try:
                r = requests.post(f"{COMERCIAPP_API}/api/bot/deduplicar", headers=_ca_headers(), json={}, timeout=120)
                if r.status_code == 200:
                    d = r.json()
                    tg(f"✅ *Duplicados eliminados* — {d.get('borrados',0)} borrados en {d.get('grupos_afectados',0)} productos.\n\nRevisá la web: debería quedar 1 de cada uno.")
                else:
                    tg(f"❌ Error al deduplicar: HTTP {r.status_code}\n`{r.text[:200]}`")
            except Exception as e:
                tg(f"❌ Error: `{e}`")
        threading.Thread(target=_dedup, daemon=True).start()
        return
    elif cmd[0] == "/test_proxy":
        def _test_proxy():
            res = ["🔬 *Diagnóstico de conexión al proveedor*\n"]
            proxy = _get_residential_proxy()
            # 0) Config
            res.append(f"*Proxy configurado:* {'SÍ' if proxy else 'NO — faltan PROXY_HOST/PORT/USER/PASS'}")
            if proxy:
                # Mostrar host:puerto sin exponer user/pass
                res.append(f"  `{PROXY_HOST}:{PROXY_PORT}`")
            res.append(f"*curl-cffi:* {'✅ disponible' if CURL_CFFI_OK else '❌ no instalado'}")
            res.append("")

            # 1) Sin proxy (directo) — para ver si Cloudflare bloquea
            res.append("*1) Directo (sin proxy):*")
            try:
                if CURL_CFFI_OK:
                    r = cf_requests.get("https://rxzweb.com/wp-json/wc/store/v1/products",
                                        params={"per_page": 1}, impersonate="chrome124", timeout=20)
                    res.append(f"  HTTP {r.status_code} " + ("✅ pasa" if r.status_code == 200 else "⚠️ (Cloudflare suele dar 202/403 acá)"))
                else:
                    res.append("  ⏭️ (curl-cffi no disponible)")
            except Exception as e:
                res.append(f"  ❌ {type(e).__name__}: {str(e)[:120]}")
            res.append("")

            # 2) Con proxy — el que usa el bot
            res.append("*2) Con proxy residencial:*")
            if not proxy:
                res.append("  ⏭️ sin proxy configurado")
            elif not CURL_CFFI_OK:
                res.append("  ⏭️ curl-cffi no disponible")
            else:
                try:
                    r = cf_requests.get("https://rxzweb.com/", impersonate="chrome124", proxy=proxy, timeout=20)
                    res.append(f"  Calentamiento HTTP {r.status_code} " + ("✅" if r.status_code in (200, 202, 403) else "⚠️"))
                    time.sleep(1)
                    r2 = cf_requests.get("https://rxzweb.com/wp-json/wc/store/v1/products",
                                         params={"per_page": 1}, impersonate="chrome124", proxy=proxy, timeout=30)
                    if r2.status_code == 200:
                        try:
                            n = len(r2.json())
                            res.append(f"  Scrape HTTP 200 ✅ — devolvió {n} producto(s)")
                        except Exception:
                            res.append("  Scrape HTTP 200 pero respuesta no-JSON ⚠️")
                    else:
                        res.append(f"  Scrape HTTP {r2.status_code} ⚠️")
                except Exception as e:
                    err = str(e)
                    res.append(f"  ❌ {type(e).__name__}: {err[:140]}")
                    if "WRONG_VERSION_NUMBER" in err:
                        res.append("  💡 *Esto es proxy caído/sin saldo* o puerto/protocolo mal. Revisá ThorData.")
                    elif "407" in err or "authentication" in err.lower():
                        res.append("  💡 *User/pass del proxy incorrectos* (407).")
                    elif "timed out" in err.lower() or "timeout" in err.lower():
                        res.append("  💡 *El proxy no responde* — host/puerto mal o servicio caído.")
            res.append("")
            res.append("_Si (1) falla con 202/403 pero (2) también falla → problema del proxy._")
            tg("\n".join(res))
        tg("🔬 Probando conexión... (tarda ~30s)")
        threading.Thread(target=_test_proxy, daemon=True).start()
        return
    elif cmd[0] == "/ayuda":
        tg(AYUDA)
    elif cmd[0] == "/estado_api":
        if _token:
            tg(f"✅ *Token activo*\nStore ID: `{_store_id}`\nToken: `{_token[:12]}...`")
        else:
            tg("❌ Sin token. Mandá el `?code=` para obtenerlo.")
    elif cmd[0] == "/borrar_token":
        _token = _store_id = None
        db = leer_db(); db.pop("api_token",None); db.pop("api_user_id",None)
        escribir_db(db); tg("🗑️ Token eliminado.")
    elif cmd[0] == "/listar":
        if not _token: tg("❌ Necesito el token primero."); return
        tg("⏳ Cargando catálogo...")
        catalogo = obtener_catalogo(forzar=True)
        if not catalogo: tg("No encontré productos."); return
        lineas = []
        for p in catalogo[:50]:
            cv   = len(p["variantes"])
            icon = "✅" if p["published"] else "🚫"
            extra = f"({cv} var.)" if cv > 0 else "(sin var.)"
            lineas.append(icon + " *" + p["nombre"] + "* " + extra + " — $" + str(int(p["precio_base"])) + " (ID:" + str(p["id"]) + ")")
        msg = f"📦 *{len(catalogo)} productos:*\n\n" + "\n".join(lineas)
        if len(catalogo) > 50: msg += f"\n\n_...y {len(catalogo)-50} más_"
        tg(msg)
    elif cmd[0] == "/sync_total":
        if not _token: tg("❌ Necesito el token primero."); return
        threading.Thread(target=run_sync_total, daemon=True).start()
    elif cmd[0] == "/fix_envio_gratis":
        if not _token: tg("❌ Necesito el token primero."); return
        threading.Thread(target=run_fix_envio_gratis, daemon=True).start()
    elif cmd[0] == "/ocultar":
        if not _token: tg("❌ Necesito el token primero."); return
        nombre = " ".join(texto.split()[1:])
        if not nombre: tg("Uso: `/ocultar Nombre`"); return
        prod = next((p for p in obtener_catalogo() if match(normalizar(nombre),p["nombre_norm"])),None)
        if prod:
            if set_visibilidad(prod["id"],False): tg(f"🚫 *{prod['nombre']}* ocultado.")
            else: tg("❌ No pude ocultar.")
        else: tg(f"❌ No encontré *{nombre}*.")
    elif cmd[0] == "/publicar":
        if not _token: tg("❌ Necesito el token primero."); return
        nombre = " ".join(texto.split()[1:])
        if not nombre: tg("Uso: `/publicar Nombre`"); return
        prod = next((p for p in obtener_catalogo() if match(normalizar(nombre),p["nombre_norm"])),None)
        if prod:
            if set_visibilidad(prod["id"],True): tg(f"✅ *{prod['nombre']}* publicado.")
            else: tg("❌ No pude publicar.")
        else: tg(f"❌ No encontré *{nombre}*.")
    elif cmd[0] == "/stock":
        if not _token: tg("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3: tg("Uso: `/stock Nombre 10`"); return
        try: nuevo=int(partes[-1]); nombre=" ".join(partes[1:-1])
        except ValueError: tg("El último parámetro debe ser un número."); return
        prod = next((p for p in obtener_catalogo() if match(normalizar(nombre),p["nombre_norm"])),None)
        if prod:
            if prod["variantes"]:
                ok = all(set_stock_variante(v["id"],nuevo) for v in prod["variantes"])
            else:
                ok = set_stock_producto(prod["id"],nuevo)
            tg(f"📦 *{prod['nombre']}* → stock *{nuevo}*." if ok else "❌ No pude actualizar.")
        else: tg(f"❌ No encontré *{nombre}*.")
    elif cmd[0] == "/precio":
        if not _token: tg("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3: tg("Uso: `/precio Nombre 9999`"); return
        try: nuevo=int(partes[-1]); nombre=" ".join(partes[1:-1])
        except ValueError: tg("El último parámetro debe ser un número."); return
        prod = next((p for p in obtener_catalogo() if match(normalizar(nombre),p["nombre_norm"])),None)
        if prod:
            if prod["variantes"]:
                ok = all(set_precio_variante(v["id"],nuevo) for v in prod["variantes"])
            else:
                ok = set_precio_producto(prod["id"],nuevo)
            tg(f"💲 *{prod['nombre']}* → *${nuevo:,}*." if ok else "❌ No pude actualizar.")
        else: tg(f"❌ No encontré *{nombre}*.")
    elif cmd[0] == "/exportar_precios":
        tg("⏳ Generando Excel...")
        data, msg = generar_excel()
        if data:
            fecha = datetime.now().strftime("%d-%m-%Y")
            if not tg_doc(data, f"precios_{fecha}.xlsx", caption=msg):
                tg("❌ Excel generado pero falló el envío.")
        else: tg(msg)
    elif cmd[0] == "/ciclo":
        tg(f"🔄 *{_nt('Ciclo manual iniciado')}* (corre aunque el scraping esté apagado)")
        def _ciclo_manual():
            db = leer_db()
            era_activo = db.get("scraping_activo", False)
            if not era_activo:
                db["scraping_activo"] = True; escribir_db(db)
            try:
                if ciclo_monitoreo() is False: tg("ℹ️ Ya hay un ciclo corriendo: esperá que termine.")
            finally:
                if not era_activo:
                    db2 = leer_db(); db2["scraping_activo"] = False; escribir_db(db2)
        threading.Thread(target=_ciclo_manual, daemon=True).start()
    elif cmd[0] == "/debug_ordenes":
        if not _token: tg("❌ Necesito el token primero."); return
        tg("🔄 Probando GET /orders directo...")
        try:
            r = requests.get(f"{API_BASE}/orders", headers=_h(), params={"per_page":5}, timeout=20)
            tg(f"✅ Respuesta recibida\nHTTP {r.status_code}\n`{r.text[:300]}`")
        except Exception as e:
            tg(f"❌ Excepción real: `{type(e).__name__}: {e}`")
    elif cmd[0] == "/buscar_todos_videos":
        if not _token: tg("Necesito el token primero."); return
        threading.Thread(target=run_buscar_todos_videos, daemon=True).start()
        tg("Buscando videos en YouTube para todos los productos sin video...")
    elif cmd[0] == "/set_video":
        if not _token: tg("Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3: tg("Uso: `/set_video Nombre del producto https://youtube.com/...`"); return
        url = partes[-1]
        nombre = " ".join(partes[1:-1])
        if "youtube.com" not in url and "youtu.be" not in url:
            tg("La URL debe ser de YouTube."); return
        prod = next((p for p in obtener_catalogo() if match(normalizar(nombre), p["nombre_norm"])), None)
        if not prod:
            tg(f"No encontre *{nombre}* en tu catalogo."); return
        if set_video_producto(prod["id"], url):
            tg(f"Video agregado a *{prod['nombre']}*")
        else:
            tg("No pude agregar el video.")
    elif cmd[0] == "/generar_descripcion":
        if not _token: tg("Necesito el token primero."); return
        if not GROQ_API_KEY:
            tg("Falta `GROQ_API_KEY` en Railway Variables."); return
        nombre = " ".join(texto.split()[1:]).strip()
        if not nombre: tg("Uso: `/generar_descripcion Nombre del producto`"); return
        prod = next((p for p in obtener_catalogo() if match(normalizar(nombre), p["nombre_norm"])), None)
        if not prod:
            tg(f"No encontre *{nombre}* en tu catalogo."); return
        tg(f"Generando descripcion para *{prod['nombre']}*...")
        desc, err = generar_descripcion_ia(prod["nombre"], int(prod["precio_base"]))
        if err:
            tg(f"Error: {err}"); return
        if set_descripcion_producto(prod["id"], desc):
            tg(f"*{prod['nombre']}*\n\n{desc}")
        else:
            tg("No pude subir la descripcion a Tienda Nube.")
    elif cmd[0] == "/generar_todas_descripciones":
        if not _token: tg("Necesito el token primero."); return
        if not GROQ_API_KEY:
            tg("Falta `GROQ_API_KEY` en Railway Variables."); return
        threading.Thread(target=run_generar_todas_descripciones, daemon=True).start()
        tg(f"Iniciando generacion masiva de descripciones...")
    elif cmd[0] == "/test_scraperapi":
        if not SCRAPERAPI_KEY:
            tg("❌ Falta `SCRAPERAPI_KEY` en Railway Variables."); return
        tg("🔄 Probando ScraperAPI contra el proveedor...")
        r = _via_scraperapi(PROV_API, params={"per_page":1,"page":1})
        if r and r.status_code == 200:
            try:
                data = r.json()
                nombre = data[0].get("name","?") if data else "?"
                tg(f"✅ ScraperAPI funciona. Producto de prueba: *{nombre}*")
            except Exception as e:
                tg(f"⚠️ HTTP 200 pero no pude parsear JSON: {e}")
        else:
            tg(f"❌ ScraperAPI HTTP {r.status_code if r is not None else 'Sin respuesta'}")
    elif cmd[0] == "/registrar_webhooks":
        if not _token: tg("❌ Necesito el token primero."); return
        if not WEBHOOK_BASE_URL:
            tg("❌ Falta `WEBHOOK_BASE_URL` en Railway Variables.\n"
               "Valor: `https://bot-dropshipping-production.up.railway.app`"); return
        tg("🔄 Registrando webhooks en Tienda Nube...")
        resultados = []
        for evento in ["order/created", "order/paid"]:
            try:
                r = requests.post(f"{API_BASE}/webhooks", headers=_h(),
                    json={"event": evento, "url": f"{WEBHOOK_BASE_URL}/webhook"}, timeout=30)
                if r is not None and r.status_code in (200, 201):
                    resultados.append(f"✅ `{evento}`")
                else:
                    codigo = r.status_code if r is not None else "Sin respuesta"
                    detalle = r.text[:100] if r is not None else ""
                    resultados.append(f"❌ `{evento}` — HTTP {codigo}: {detalle}")
            except Exception as e:
                resultados.append(f"❌ `{evento}` — Error: {e}")
        tg("*Webhooks:*\n" + "\n".join(resultados))
    elif cmd[0] == "/ver_webhooks":
        if not _token: tg("❌ Necesito el token primero."); return
        r = _get(f"{API_BASE}/webhooks")
        if r and r.status_code == 200:
            hooks = r.json()
            results = hooks.get("results", hooks) if isinstance(hooks, dict) else hooks
            if not results:
                tg("ℹ️ No hay webhooks registrados."); return
            lineas = [f"• `{h.get('event')}` (ID:{h.get('id')})\n  → {h.get('url')}" for h in results]
            tg("🔗 *Webhooks activos:*\n\n" + "\n\n".join(lineas))
        else:
            tg(f"❌ HTTP {r.status_code if r is not None else 'None'}")
    elif cmd[0] == "/borrar_webhook":
        if not _token: tg("❌ Necesito el token primero."); return
        wid = texto.split()[1] if len(texto.split()) > 1 else ""
        if not wid: tg("Uso: /borrar_webhook ID"); return
        r = requests.delete(f"{API_BASE}/webhooks/{wid}", headers=_h(), timeout=30)
        tg(f"✅ Webhook {wid} borrado." if r and r.status_code in (200,204) else f"❌ HTTP {r.status_code if r is not None else 'None'}")
    elif cmd[0] == "/productos_sin_cargar":
        if not _token: tg("❌ Necesito el token primero."); return
        modo_todo = len(cmd) > 1 and cmd[1] == "todo"
        threading.Thread(target=run_productos_sin_cargar, args=(modo_todo,), daemon=True).start()
    elif cmd[0] == "/ver_ofertas_proveedor":
        threading.Thread(target=run_ver_ofertas_proveedor, daemon=True).start()
    elif cmd[0] == "/aplicar_ofertas":
        db = leer_db()
        ofertas = db.get("ofertas_pendientes", {})
        if not ofertas:
            tg("ℹ️ No hay ofertas pendientes de aplicar."); return
        lista = list(ofertas.items())
        partes = texto.split()[1:]
        if not partes:
            tg("Uso: `/aplicar_ofertas todos` o `/aplicar_ofertas 1 3`"); return
        if partes[0] == "todos":
            seleccion = list(range(len(lista)))
        else:
            seleccion = []
            for x in partes:
                try:
                    n = int(x) - 1
                    if 0 <= n < len(lista): seleccion.append(n)
                except ValueError: pass
        if not seleccion:
            tg("❌ Número inválido."); return
        catalogo = obtener_catalogo()
        idx = construir_indice({k:v for k,v in ofertas.items()})
        aplicadas = []
        for i in seleccion:
            base_norm, datos_prov = lista[i]
            prod = next((p for p in catalogo if match(p["nombre_norm"], base_norm)), None)
            if not prod:
                aplicadas.append(f"• *{datos_prov['nombre_real']}* — No encontrado en tu tienda"); continue
            ok, p_min, p_max = sincronizar_precios(prod, datos_prov)
            if ok:
                rango = f"${p_min:,}" if p_min==p_max else f"${p_min:,}–${p_max:,}"
                aplicadas.append(f"• *{prod['nombre']}*\n  Precio oferta: {rango}")
            else:
                aplicadas.append(f"• *{prod['nombre']}* — Error al actualizar")
        db["ofertas_pendientes"] = {}
        escribir_db(db)
        tg("✅ *Ofertas aplicadas:*\n\n" + "\n\n".join(aplicadas))
    elif cmd[0] == "/debug_match":
        nombre = " ".join(texto.split()[1:]).strip()
        if not nombre: tg("Uso: /debug_match Nombre del producto"); return
        nombre_norm = normalizar(nombre)
        db = leer_db(); prov = db.get("productos_proveedor",{})
        if not prov: tg("Sin datos del proveedor."); return
        idx = construir_indice(prov)
        base_norm, datos_prov = buscar_en_indice(nombre_norm, idx)
        lineas = ["Debug: " + nombre, ""]
        if datos_prov:
            p_o = precio_obj(datos_prov["precio_base"])
            lineas += ["PROVEEDOR: encontrado",
                       "  Base: " + base_norm,
                       "  Precio base: $" + str(datos_prov["precio_base"]) + " -> Web: $" + str(p_o),
                       "  Stock base: " + str(datos_prov.get("stock_base","?")),
                       "  Variantes prov: " + str(len(datos_prov["variantes"]))]
            for vn, vd in list(datos_prov["variantes"].items())[:5]:
                lineas.append("    [" + vn[:35] + "]: $" + str(vd["precio"]) + " st:" + str(vd.get("stock","?")))
        else:
            lineas.append("PROVEEDOR: NO encontrado")
        lineas.append("")
        catalogo = obtener_catalogo() if _token else []
        prod = next((p for p in catalogo if match(nombre_norm,p["nombre_norm"])),None)
        if prod and datos_prov:
            vars_prov_idx = datos_prov.get("variantes", {})
            lineas += ["CATALOGO API: encontrado",
                       "  ID: " + str(prod["id"]),
                       "  Nombre: " + prod["nombre"],
                       "  Variantes API: " + str(len(prod["variantes"])),
                       "  Precio actual: $" + str(int(prod["precio_base"])),
                       "  Matching variantes:"]
            for v in prod["variantes"][:8]:
                vnom = normalizar(v["nombre"])
                match_prov = next(((pv,pd) for pv,pd in vars_prov_idx.items() if match(vnom,pv)), None)
                if match_prov:
                    p_calc = precio_obj(match_prov[1]["precio"])
                    lineas.append("    OK " + v["nombre"][:30] + " -> $" + str(p_calc))
                else:
                    p_fb = precio_obj(datos_prov["precio_base"])
                    lineas.append("    FB " + v["nombre"][:30] + " -> $" + str(p_fb))
        elif prod:
            lineas += ["CATALOGO API: encontrado (sin datos prov)",
                       "  ID: " + str(prod["id"]),
                       "  Nombre: " + prod["nombre"],
                       "  Variantes: " + str(len(prod["variantes"])),
                       "  Precio actual: $" + str(int(prod["precio_base"]))]
        else:
            lineas.append("CATALOGO API: " + ("NO encontrado" if _token else "sin token"))
        tg("\n".join(lineas))
    elif cmd[0] == "/debug_producto":
        if not _token: tg("Sin token."); return
        pid = texto.split()[1] if len(texto.split()) > 1 else ""
        if not pid: tg("Uso: /debug_producto ID"); return
        r = _get(f"{API_BASE}/products/{pid}")
        if not r or r.status_code != 200: tg("HTTP " + str(r.status_code if r is not None else 0)); return
        d = r.json()
        sep = chr(10)
        info = [k + ": " + str(v) for k,v in d.items() if not isinstance(v,(dict,list))]
        tg("Producto " + str(pid) + ":" + sep + sep.join(info[:25]))
    elif cmd[0] == "/debug_env":
        t = os.environ.get("API_TOKEN",""); u = os.environ.get("API_USER_ID","")
        lineas = [
            "TELEGRAM_TOKEN: " + ("OK" if TELEGRAM_TOKEN else "NO"),
            "CHAT_ID: "        + ("OK" if CHAT_ID else "NO"),
            "CLIENT_ID: "      + ("OK" if CLIENT_ID else "NO"),
            "API_TOKEN env: "  + (t[:10]+"..." if t else "NO"),
            "API_USER_ID env: "+ (u if u else "NO"),
            "Token memoria: "  + (str(_token)[:10]+"..." if _token else "NONE"),
            "UserID memoria: " + (_store_id or "NONE"),
            "NOMBRE_TIENDA: "  + NOMBRE_TIENDA,
            "MARGEN: "         + str(MARGEN),
        ]
        tg("Diagnóstico:\n" + "\n".join(lineas))
    elif cmd[0] == "/confirmar_pedido":
        partes = texto.split()
        if len(partes) < 2:
            tg("Uso: /confirmar_pedido NUMERO_ORDEN"); return
        num = partes[1]
        db  = leer_db()
        pp  = db.get("pedido_pendiente", {})
        if not pp or str(pp.get("num_orden")) != str(num):
            tg(f"No hay pedido pendiente #{num}."); return
        tg(f"🔄 Confirmando pedido #{num} al proveedor...")
        orden_prov = hacer_pedido_proveedor(pp["items"], pp["cliente"], pp.get("nota",""))
        if orden_prov:
            tg(f"✅ Pedido #{num} enviado al proveedor como #{orden_prov}")
            db.pop("pedido_pendiente", None)
            db.setdefault("ordenes", {})[str(num)] = {"orden_prov": orden_prov}
            escribir_db(db)
        else:
            tg(f"❌ No pude hacer el pedido #{num}. Intentá manualmente.")
    else:
        tg("❓ Comando no reconocido. Mandá `/ayuda`.")

# ── Loop Telegram ─────────────────────────────────────────────────────────────
def escuchar_telegram():
    if not TELEGRAM_TOKEN: return
    offset = 0; url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    # Borra cualquier webhook de Telegram que bloquee getUpdates (por eso el bot no recibia comandos)
    try:
        wr = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook?drop_pending_updates=false", timeout=10)
        print(f"🧹 deleteWebhook: {wr.status_code} {wr.text[:80]}")
    except Exception as e:
        print(f"⚠️ deleteWebhook: {e}")
    print("📡 Telegram activo...")
    while True:
        try:
            r = requests.get(f"{url}?offset={offset}&timeout=10", timeout=15)
            if r.status_code != 200:
                print(f"⚠️ Telegram getUpdates HTTP {r.status_code}: {r.text[:150]}")
            if r.status_code == 200:
                for u in r.json().get("result",[]):
                    offset = u["update_id"] + 1
                    msg = u.get("message",{}); texto = msg.get("text","")
                    if str(msg.get("chat",{}).get("id","")) == CHAT_ID and texto:
                        print(f"📨 {texto[:60]}")
                        try: procesar_cmd(texto)
                        except Exception as e: print(f"❌ Cmd: {e}")
                    cb = u.get("callback_query",{})
                    if cb and str(cb.get("message",{}).get("chat",{}).get("id","")) == CHAT_ID:
                        cb_data = cb.get("data","")
                        cb_id   = cb.get("id","")
                        try:
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb_id}, timeout=5)
                        except Exception: pass
                        if cb_data:
                            print(f"🔘 Botón: {cb_data[:60]}")
                            try: procesar_cmd(cb_data)
                            except Exception as e: print(f"❌ Botón cmd: {e}")
        except Exception as e: print(f"⚠️ Telegram: {e}")
        time.sleep(1)

# ══════════════════════════════════════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 Bot iniciado...")
    print(f"   NOMBRE_TIENDA:   {NOMBRE_TIENDA}")
    print(f"   MARGEN:          {MARGEN} ({round((1-MARGEN)*100)}% ganancia)")
    print(f"   ComerciApp API:  {'SÍ (' + COMERCIAPP_API[:40] + '...)' if COMERCIAPP_API else 'NO — falta COMERCIAPP_API'}")
    print(f"   BOT_API_KEY:     {'SÍ' if COMERCIAPP_KEY else 'NO — falta BOT_API_KEY'}")
    print(f"   ThorData Unlocker: {'SÍ (bypass Cloudflare)' if THORDATA_TOKEN else 'NO — falta THORDATA_TOKEN'}")
    rp = _get_residential_proxy()
    if rp:
        print(f"   ✅ Proxy residencial: {PROXY_HOST}:{PROXY_PORT}")
    else:
        print(f"   ⚠️ Sin proxy residencial — usando curl-cffi directo")

    _db_ini = leer_db()
    _scr = _db_ini.get("scraping_activo", False)
    _web = "✅" if comerciapp_ok() else "❌ falta config"
    tg(f"{'🟢' if comerciapp_ok() else '🟡'} *{_nt('Bot iniciado')}*\n\n"
       f"• Scraping: {'▶️ ENCENDIDO' if _scr else '⏸️ APAGADO (arranca así tras cada deploy)'}\n"
       f"• Web ComerciApp: {_web}\n"
       f"• Margen: {MARGEN}\n\n"
       f"Mandá /encender para arrancar la sincronización.\n"
       f"Mandá /menu para el panel de botones.")

    registrar_comandos_tg()
    threading.Thread(target=escuchar_telegram, daemon=True).start()
    if FLASK_OK:
        threading.Thread(target=run_flask, daemon=True).start()
    else:
        print('⚠️ Flask no disponible — webhooks desactivados')

    def _vuelta():
        """Una vuelta del bot: ciclo + tareas periódicas + latido a la web (si el latido deja de llegar, la web avisa)."""
        try:
            ciclo_monitoreo()
            resolver("ciclo")
        except Exception as e:
            import traceback; traceback.print_exc()
            alerta("ciclo", "Error en el ciclo del bot", f"{_md(str(e))[:300]}\nSigue intentando cada {CICLO_MINUTOS} min.")
        tareas_periodicas()

    threading.Thread(target=hilo_latido, daemon=True).start()
    _vuelta()
    while True:
        time.sleep(_minutos_ciclo() * 60)
        _vuelta()
