import os
import time
import json
import requests
from bs4 import BeautifulSoup
import urllib3
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import threading
import io

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── URLs ────────────────────────────────────────────────────────────────────
URL_A            = "https://rxzweb.com/tienda/?et_per_page=-1"
URL_B            = "https://leandroid.tiendanegocio.com/productos"
DB_FILE          = "estado_productos.json"
USER_AGENT_API   = "dropshipping (lean.6roid@gmail.com)"

URL_API_BASE     = "https://developers.tiendanegocio.com/v1"
URL_API_PRODUCTS = f"{URL_API_BASE}/products"
URL_API_VARIANTS = f"{URL_API_BASE}/variants"

# ─── Variables de entorno ─────────────────────────────────────────────────────
TELEGRAM_TOKEN = None
CHAT_ID        = None
SCRAPERAPI_KEY = None
GMAIL_USER     = None
GMAIL_PASS     = None
CLIENT_ID      = None
CLIENT_SECRET  = None

for k, v in os.environ.items():
    val = v.strip()
    if "TELEGRAM_TOKEN" in k: TELEGRAM_TOKEN = val
    if "CHAT_ID"        in k: CHAT_ID        = val
    if "SCRAPERAPI_KEY" in k: SCRAPERAPI_KEY = val
    if "GMAIL_USER"     in k: GMAIL_USER     = val
    if "GMAIL_PASS"     in k: GMAIL_PASS     = val
    if "CLIENT_ID"      in k: CLIENT_ID      = val
    if "CLIENT_SECRET"  in k: CLIENT_SECRET  = val

# ─── Token API ───────────────────────────────────────────────────────────────
_api_token   = None
_api_user_id = None

def _cargar_token_desde_db():
    global _api_token, _api_user_id
    env_token   = os.environ.get("API_TOKEN")
    env_user_id = os.environ.get("API_USER_ID")
    if env_token and env_user_id:
        _api_token   = env_token.strip()
        _api_user_id = env_user_id.strip()
        print(f"✅ Token cargado desde Railway (store_id={_api_user_id})")
        return
    estado = cargar_estado_anterior()
    t = estado.get("api_token")
    u = estado.get("api_user_id")
    if t and u:
        _api_token   = t
        _api_user_id = u
        print(f"✅ Token cargado desde DB (store_id={u})")

def _guardar_token_en_db(token, user_id):
    global _api_token, _api_user_id
    _api_token   = token
    _api_user_id = user_id
    estado = cargar_estado_anterior()
    estado["api_token"]   = token
    estado["api_user_id"] = user_id
    guardar_estado_actual(estado)
    print(f"💾 Token guardado (store_id={user_id})")

# ─── Palabras de interés ─────────────────────────────────────────────────────
PALABRAS_INTERES = [
    'ma ant', 'amaoe', '2uul', 'goot wick', 'mijing', 'louwei',
    'rf4', 'jakemy', 'kailiwei', 'kslid', 'aifen', 'sugon', 'jcid', 'jc',
    'v1', 'v1s', 'v1se', 'v1 pro', 'programadora',
    'organizador', 'cinta', 'silla', 'mesa', 'puas', 'hilo', 'cepillo'
]

# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

def enviar_telegram(mensaje):
    if not mensaje or not TELEGRAM_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}, timeout=15)
        print("🚀 Telegram enviado.")
    except Exception as e:
        print(f"❌ Telegram: {e}")

def enviar_archivo_telegram(buffer_bytes, nombre_archivo, caption=""):
    if not TELEGRAM_TOKEN or not CHAT_ID: return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        resp = requests.post(url,
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"document": (nombre_archivo, buffer_bytes,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=30)
        ok = resp.status_code == 200
        print(f"{'✅' if ok else '❌'} Archivo enviado (HTTP {resp.status_code})")
        return ok
    except Exception as e:
        print(f"❌ Archivo: {e}"); return False

# ═══════════════════════════════════════════════════════════════════════════════
# OAUTH
# ═══════════════════════════════════════════════════════════════════════════════

def intercambiar_codigo_por_token(auth_code):
    payload = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
               "grant_type": "authorization_code", "code": auth_code}
    try:
        endpoint = "https://developers.tiendanegocio.com/v1/oauth/app/token"
        resp     = requests.post(endpoint, json=payload,
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT_API},
                                 timeout=30)
        print(f"OAuth HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code in (200, 201):
            api_data = resp.json().get("data", resp.json())
            token    = api_data.get("access_token")
            user_id  = str(api_data.get("store_id") or api_data.get("user_id") or "")
            if token and user_id:
                _guardar_token_en_db(token, user_id)
                return token
        return None
    except Exception as e:
        print(f"❌ OAuth: {e}"); return None

# ═══════════════════════════════════════════════════════════════════════════════
# API — TIENDA NEGOCIO
# ═══════════════════════════════════════════════════════════════════════════════

_cache_productos_api = None
_tiempo_cache_api    = 0

def _api_headers():
    return {"Authorization": f"Bearer {_api_token}",
            "User-Agent": USER_AGENT_API, "Content-Type": "application/json"}

def obtener_todos_los_productos_api(forzar_recarga=False):
    global _cache_productos_api, _tiempo_cache_api
    if not forzar_recarga and _cache_productos_api and (time.time() - _tiempo_cache_api) < 300:
        return _cache_productos_api
    if not _api_token: return []
    todos = []; pagina = 1
    print("⏳ Descargando catálogo API Tienda Negocio...")
    while True:
        try:
            resp = requests.get(f"{URL_API_PRODUCTS}?per_page=50&page={pagina}&with_variants=true",
                                headers=_api_headers(), timeout=20)
            if resp.status_code == 429:
                print("   ⚠️ Rate limit. Esperando 5s..."); time.sleep(5); continue
            if resp.status_code != 200:
                print(f"❌ API HTTP {resp.status_code}: {resp.text[:150]}"); break
            data = resp.json()
            lote = data.get("results", data) if isinstance(data, dict) else data
            if not lote: break
            todos.extend(lote)
            pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
            if not pagination.get("next_page") or len(lote) < 50: break
            pagina += 1; time.sleep(0.5)
        except Exception as e:
            print(f"❌ Paginando: {e}"); break
    print(f"   📦 Catálogo: {len(todos)} productos.")
    _cache_productos_api = todos
    _tiempo_cache_api    = time.time()
    return todos

def buscar_producto_api(nombre_buscado):
    for p in obtener_todos_los_productos_api():
        nombre_api = p.get("name", {})
        if isinstance(nombre_api, dict): nombre_api = next(iter(nombre_api.values()), "")
        if son_coincidentes_inteligentes(str(nombre_api), nombre_buscado):
            variantes = p.get("variants", [])
            return p.get("id"), variantes[0].get("id") if variantes else None
    return None, None

def modificar_precio_api(variant_id, nuevo_precio):
    if not _api_token: return False
    try:
        resp = requests.put(f"{URL_API_VARIANTS}/{variant_id}",
                            json={"price": str(nuevo_precio)},
                            headers=_api_headers(), timeout=15)
        if resp.status_code == 429:
            time.sleep(3)
            resp = requests.put(f"{URL_API_VARIANTS}/{variant_id}",
                                json={"price": str(nuevo_precio)},
                                headers=_api_headers(), timeout=15)
        ok = resp.status_code in (200, 201)
        if not ok: print(f"❌ Precio HTTP {resp.status_code}: {resp.text[:100]}")
        return ok
    except Exception as e:
        print(f"❌ Precio: {e}"); return False

def modificar_stock_api(variant_id, nuevo_stock):
    """Pone stock_management=true para que el stock sea visible en la tienda."""
    if not _api_token: return False
    try:
        resp = requests.put(f"{URL_API_VARIANTS}/{variant_id}",
                            json={"stock": int(nuevo_stock), "stock_management": True},
                            headers=_api_headers(), timeout=15)
        ok = resp.status_code in (200, 201)
        if not ok: print(f"❌ Stock HTTP {resp.status_code}: {resp.text[:100]}")
        return ok
    except Exception as e:
        print(f"❌ Stock: {e}"); return False

def restaurar_stock_libre_api(variant_id):
    """Cuando el proveedor recupera stock: desactiva stock_management para volver a 'disponible'."""
    if not _api_token: return False
    try:
        resp = requests.put(f"{URL_API_VARIANTS}/{variant_id}",
                            json={"stock_management": False},
                            headers=_api_headers(), timeout=15)
        ok = resp.status_code in (200, 201)
        if not ok: print(f"❌ Restaurar stock HTTP {resp.status_code}: {resp.text[:100]}")
        return ok
    except Exception as e:
        print(f"❌ Restaurar stock: {e}"); return False

def publicar_producto_api(product_id):
    if not _api_token: return False
    try:
        resp = requests.put(f"{URL_API_PRODUCTS}/{product_id}",
                            json={"published": True}, headers=_api_headers(), timeout=15)
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"❌ Publicar: {e}"); return False

def ocultar_producto_api(product_id):
    if not _api_token: return False
    try:
        resp = requests.put(f"{URL_API_PRODUCTS}/{product_id}",
                            json={"published": False}, headers=_api_headers(), timeout=15)
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"❌ Ocultar: {e}"); return False

# ═══════════════════════════════════════════════════════════════════════════════
# SINCRONIZACIÓN COMPLETA (inicial o manual)
# ═══════════════════════════════════════════════════════════════════════════════

def sincronizacion_completa(prod_a, prod_b):
    """
    Recorre TODOS los productos de la web, compara con el proveedor y:
    - Actualiza precios (proveedor / 0.78 redondeado)
    - Marca sin stock los que el proveedor no tiene
    Devuelve el dict 'sincronizados' actualizado y envía un resumen por Telegram.
    """
    sincronizados = {}
    lineas_precios    = []
    lineas_sin_stock  = []
    lineas_sin_match  = []
    errores           = []

    total = len(prod_b)
    print(f"🔄 Sincronización completa: {total} productos a procesar...")

    for i, (clave_b, datos_b) in enumerate(prod_b.items(), 1):
        nombre_real = datos_b["nombre_real"]
        print(f"   [{i}/{total}] {nombre_real[:50]}")

        # Buscar en proveedor
        datos_a = None
        for clave_a, da in prod_a.items():
            if son_coincidentes_inteligentes(clave_b, clave_a):
                datos_a = da; break
        if datos_a is None:
            variantes = [d for c, d in prod_a.items()
                         if son_coincidentes_inteligentes(clave_b, d.get("nombre_base_proveedor", ""))]
            if variantes: datos_a = min(variantes, key=lambda x: x["precio"])

        if datos_a is None:
            # Sin match en proveedor → marcar sin stock
            if _api_token:
                product_id, variant_id = buscar_producto_api(nombre_real)
                if product_id and variant_id:
                    if modificar_stock_api(variant_id, 0):
                        sincronizados[nombre_real] = {"sin_stock": True}
                        lineas_sin_stock.append(f"• *{nombre_real}*")
                    else:
                        errores.append(nombre_real)
                else:
                    lineas_sin_match.append(nombre_real)
            else:
                sincronizados[nombre_real] = {"sin_stock": True}
                lineas_sin_stock.append(f"• *{nombre_real}* _(sin API)_")
            time.sleep(0.3)
            continue

        # Tiene match → actualizar precio
        precio_objetivo = redondear_precio(datos_a["precio"] / 0.78)

        if _api_token:
            product_id, variant_id = buscar_producto_api(nombre_real)
            if product_id and variant_id:
                ok_precio = modificar_precio_api(variant_id, precio_objetivo)
                if ok_precio:
                    sincronizados[nombre_real] = {"precio": precio_objetivo}
                    if datos_b["precio"] != precio_objetivo:
                        lineas_precios.append(
                            f"• *{nombre_real}*\n"
                            f"  Proveedor: ${datos_a['precio']:,} | "
                            f"Anterior: ${datos_b['precio']:,} | "
                            f"*Nuevo: ${precio_objetivo:,}*"
                        )
                else:
                    errores.append(nombre_real)
            else:
                lineas_sin_match.append(nombre_real)
        else:
            sincronizados[nombre_real] = {"precio": precio_objetivo}
            if datos_b["precio"] != precio_objetivo:
                lineas_precios.append(
                    f"• *{nombre_real}*\n"
                    f"  Proveedor: ${datos_a['precio']:,} | "
                    f"Anterior: ${datos_b['precio']:,} | "
                    f"*Sugerido: ${precio_objetivo:,}*"
                )
        time.sleep(0.3)

    # ── Notificación resumen ──────────────────────────────────────────────
    resumen = f"✅ *Sincronización completa terminada*\n\n"
    resumen += f"📊 Total procesados: *{total}*\n"
    resumen += f"💲 Precios actualizados: *{len(lineas_precios)}*\n"
    resumen += f"📦 Marcados sin stock: *{len(lineas_sin_stock)}*\n"
    if errores:       resumen += f"⚠️ Errores: *{len(errores)}*\n"
    if lineas_sin_match: resumen += f"❓ Sin match en API: *{len(lineas_sin_match)}*\n"
    enviar_telegram(resumen)

    # Detalles de precios (en bloques de 30 para no exceder el límite de Telegram)
    if lineas_precios:
        for i in range(0, len(lineas_precios), 30):
            bloque = lineas_precios[i:i+30]
            accion = "🤖 *Precios actualizados:*" if _api_token else "📉 *Precios a actualizar (sin API):*"
            enviar_telegram(f"{accion}\n\n" + "\n\n".join(bloque))

    if lineas_sin_stock:
        for i in range(0, len(lineas_sin_stock), 30):
            bloque = lineas_sin_stock[i:i+30]
            accion = "🤖 *Marcados sin stock:*" if _api_token else "⚠️ *Sin stock en proveedor:*"
            enviar_telegram(f"{accion}\n\n" + "\n".join(bloque))

    return sincronizados

# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ═══════════════════════════════════════════════════════════════════════════════

def redondear_precio(precio):
    if precio >= 100000: return round(precio / 1000) * 1000
    elif precio >= 10000: return round(precio / 500) * 500
    elif precio >= 1000:  return round(precio / 100) * 100
    else:                 return round(precio / 50) * 50

def generar_excel_precios():
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return None, "❌ Falta openpyxl en requirements.txt."

    estado = cargar_estado_anterior()
    prod_a = estado.get("productos_a", {})
    prod_b = estado.get("productos_b", {})
    if not prod_a: return None, "❌ Sin datos del proveedor todavía."
    if not prod_b: return None, "❌ Sin datos de tu tienda todavía."

    columnas = [
        "Hash", "Nombre del producto", "Precio", "Oferta", "Stock",
        "Visibilidad (Visible o Oculto)", "Descripción", "SKU",
        "Peso en KG", "Alto en CM", "Ancho en CM", "Profundidad en CM",
        "Nombre de variante #1", "Opción de variante #1",
        "Nombre de variante #2", "Opción de variante #2",
        "Nombre de variante #3", "Opción de variante #3",
        "Categorías > Subcategorías > … > Subcategorías"
    ]
    filas = []; actualizados = sin_match = igual_precio = 0

    for clave_b, datos_b in prod_b.items():
        datos_a = None
        for clave_a, da in prod_a.items():
            if son_coincidentes_inteligentes(clave_b, clave_a):
                datos_a = da; break
        if datos_a is None:
            variantes = [d for c, d in prod_a.items()
                         if son_coincidentes_inteligentes(clave_b, d.get("nombre_base_proveedor", ""))]
            if variantes: datos_a = min(variantes, key=lambda x: x["precio"])
        if datos_a is None:
            sin_match += 1; continue
        precio_nuevo = redondear_precio(datos_a["precio"] / 0.78)
        if precio_nuevo == datos_b["precio"]:
            igual_precio += 1; continue
        filas.append({"Hash": clave_b, "Nombre del producto": datos_b["nombre_real"],
                      "Precio": precio_nuevo,
                      **{c: "" for c in columnas if c not in ["Hash","Nombre del producto","Precio"]}})
        actualizados += 1

    if not filas:
        return None, f"ℹ️ Sin cambios.\n• {igual_precio} ya correctos\n• {sin_match} sin match"

    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Productos"
    ws.append(columnas)
    hf = PatternFill("solid", fgColor="1F6B3B")
    hfont = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    for cell in ws[1]:
        cell.fill = hf; cell.font = hfont
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 25
    fp = [PatternFill("solid", fgColor="F0F7F2"), PatternFill("solid", fgColor="FFFFFF")]
    fn = Font(name="Arial", size=9); fv = Font(name="Arial", size=9, bold=True, color="1F6B3B")
    for i, fila in enumerate(filas, start=2):
        ws.append([fila.get(c, "") for c in columnas])
        for cell in ws[i]: cell.fill = fp[i%2]; cell.font = fn
        ws.cell(row=i, column=3).font = fv
        ws.cell(row=i, column=3).number_format = '#,##0'
    for col, w in {'A':38,'B':48,'C':12,'D':8,'E':8,'F':12,'G':8,'H':8,'I':8,
                   'J':8,'K':8,'L':8,'M':20,'N':22,'O':20,'P':22,'Q':20,'R':22,'S':30}.items():
        ws.column_dimensions[col].width = w
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.getvalue(), (
        f"✅ Excel generado:\n• *{actualizados}* actualizados\n"
        f"• *{igual_precio}* ya correctos\n• *{sin_match}* sin match\n\n"
        f"Importalo: *Productos → Importar y exportar → Importar*"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# COMANDOS TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

AYUDA_MSG = """
🤖 *Comandos disponibles:*

🔑 *Token / API*
`?code=XXXX` — Canjear código OAuth
`/estado_api` — Ver token activo
`/borrar_token` — Eliminar token

📦 *Productos (requiere token)*
`/listar` — Ver productos de tu tienda
`/ocultar NOMBRE` — Ocultar producto
`/publicar NOMBRE` — Publicar producto oculto
`/stock NOMBRE CANTIDAD` — Cambiar stock
`/precio NOMBRE VALOR` — Cambiar precio

🔄 *Sincronización*
`/sync_total` — Actualizar TODOS los precios y stock ahora
`/ciclo` — Forzar ciclo de monitoreo
`/exportar_precios` — Excel precios +22%

❓ `/ayuda` — Este mensaje
""".strip()

def procesar_comando(texto):
    global _api_token, _api_user_id
    texto = texto.strip()

    if "?code=" in texto:
        auth_code = texto.split("?code=")[1].split("&")[0].strip()
        enviar_telegram("🔄 Canjeando código OAuth...")
        token = intercambiar_codigo_por_token(auth_code)
        if token:
            enviar_telegram(
                f"✅ *¡Token obtenido!*\n\n"
                f"Guardá en Railway → Variables:\n"
                f"• `API_USER_ID` = `{_api_user_id}`\n"
                f"• `API_TOKEN` = `{token}`"
            )
        else:
            enviar_telegram("❌ Token fallido. Código vencido (dura 1 min).")
        return

    cmd = texto.lower().split()
    if not cmd: return

    if cmd[0] == "/ayuda":
        enviar_telegram(AYUDA_MSG)

    elif cmd[0] == "/estado_api":
        if _api_token:
            enviar_telegram(f"✅ *Token activo*\nStore ID: `{_api_user_id}`\nToken: `{_api_token[:12]}...`")
        else:
            enviar_telegram("❌ Sin token. Mandá el `?code=` para obtenerlo.")

    elif cmd[0] == "/borrar_token":
        _api_token = _api_user_id = None
        estado = cargar_estado_anterior()
        estado.pop("api_token", None); estado.pop("api_user_id", None)
        guardar_estado_actual(estado)
        enviar_telegram("🗑️ Token eliminado.")

    elif cmd[0] == "/listar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        enviar_telegram("⏳ Buscando productos...")
        productos = obtener_todos_los_productos_api(forzar_recarga=True)
        if not productos:
            enviar_telegram("No encontré productos o hubo un error."); return
        lineas = []
        for p in productos[:50]:
            nombre = p.get("name", {})
            if isinstance(nombre, dict): nombre = next(iter(nombre.values()), "")
            variantes = p.get("variants", [])
            stocks = [str(v.get("stock")) if v.get("stock") is not None else "libre" for v in variantes]
            stock_str = "/".join(stocks[:3]) if stocks else "?"
            precio    = variantes[0].get("price", p.get("price","?")) if variantes else "?"
            published = "✅" if p.get("published") else "🚫"
            lineas.append(f"{published} *{nombre}* — ${precio} — Stock: {stock_str}")
        msg = f"📦 *{len(productos)} productos:*\n\n" + "\n".join(lineas)
        if len(productos) > 50: msg += f"\n\n_...y {len(productos)-50} más_"
        enviar_telegram(msg)

    elif cmd[0] == "/sync_total":
        enviar_telegram("🔄 *Iniciando sincronización completa...*\nEsto puede tardar unos minutos.")
        def _run_sync():
            estado  = cargar_estado_anterior()
            prod_a  = estado.get("productos_a", {})
            prod_b  = estado.get("productos_b", {})
            if not prod_a or not prod_b:
                enviar_telegram("⚠️ El bot no tiene datos todavía. Esperá que complete un ciclo de monitoreo primero.")
                return
            # Forzar recarga del caché de la API
            obtener_todos_los_productos_api(forzar_recarga=True)
            sincronizados = sincronizacion_completa(prod_a, prod_b)
            estado["sincronizados"] = sincronizados
            guardar_estado_actual(estado)
        threading.Thread(target=_run_sync, daemon=True).start()

    elif cmd[0] == "/ocultar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        nombre = " ".join(texto.split()[1:])
        if not nombre:
            enviar_telegram("Uso: `/ocultar Nombre del producto`"); return
        enviar_telegram(f"🔍 Buscando *{nombre}*...")
        product_id, _ = buscar_producto_api(nombre)
        if product_id:
            if ocultar_producto_api(product_id):
                enviar_telegram(f"🚫 *{nombre}* ocultado.")
            else:
                enviar_telegram(f"❌ No pude ocultar *{nombre}*.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}*.")

    elif cmd[0] == "/publicar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        nombre = " ".join(texto.split()[1:])
        if not nombre:
            enviar_telegram("Uso: `/publicar Nombre del producto`"); return
        enviar_telegram(f"🔍 Buscando *{nombre}*...")
        product_id, _ = buscar_producto_api(nombre)
        if product_id:
            if publicar_producto_api(product_id):
                enviar_telegram(f"✅ *{nombre}* publicado.")
            else:
                enviar_telegram(f"❌ No pude publicar *{nombre}*.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}*.")

    elif cmd[0] == "/stock":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram("Uso: `/stock Nombre 10`"); return
        try:
            nuevo_stock = int(partes[-1]); nombre = " ".join(partes[1:-1])
        except ValueError:
            enviar_telegram("El último parámetro tiene que ser un número."); return
        enviar_telegram(f"🔍 Buscando *{nombre}*...")
        product_id, variant_id = buscar_producto_api(nombre)
        if product_id and variant_id:
            if modificar_stock_api(variant_id, nuevo_stock):
                enviar_telegram(f"📦 Stock de *{nombre}* → *{nuevo_stock}*.")
            else:
                enviar_telegram(f"❌ No pude actualizar el stock.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}*.")

    elif cmd[0] == "/precio":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram("Uso: `/precio Nombre 9999`"); return
        try:
            nuevo_precio = int(partes[-1]); nombre = " ".join(partes[1:-1])
        except ValueError:
            enviar_telegram("El último parámetro tiene que ser un número."); return
        enviar_telegram(f"🔍 Buscando *{nombre}*...")
        product_id, variant_id = buscar_producto_api(nombre)
        if product_id and variant_id:
            if modificar_precio_api(variant_id, nuevo_precio):
                enviar_telegram(f"💲 Precio de *{nombre}* → *${nuevo_precio:,}*.")
            else:
                enviar_telegram(f"❌ No pude actualizar el precio.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}*.")

    elif cmd[0] == "/exportar_precios":
        enviar_telegram("⏳ Generando Excel precios +22%...")
        excel_bytes, resumen = generar_excel_precios()
        if excel_bytes:
            fecha = datetime.now().strftime("%d-%m-%Y")
            if not enviar_archivo_telegram(excel_bytes, f"precios_{fecha}.xlsx", caption=resumen):
                enviar_telegram("❌ Excel generado pero falló el envío.")
        else:
            enviar_telegram(resumen)

    elif cmd[0] == "/ciclo":
        enviar_telegram("🔄 Iniciando ciclo manual...")
        threading.Thread(target=procesar_logica, daemon=True).start()

    else:
        enviar_telegram("❓ Comando no reconocido. Mandá `/ayuda`.")

def bucle_escucha_telegram():
    if not TELEGRAM_TOKEN: return
    offset = 0
    url_updates = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    print("📡 Hilo Telegram activo...")
    while True:
        try:
            resp = requests.get(f"{url_updates}?offset={offset}&timeout=10", timeout=15)
            if resp.status_code == 200:
                for update in resp.json().get("result", []):
                    offset = update.get("update_id") + 1
                    message = update.get("message", {})
                    texto   = message.get("text", "")
                    if str(message.get("chat", {}).get("id","")) != CHAT_ID or not texto: continue
                    print(f"📨 Cmd: {texto[:60]}")
                    try: procesar_comando(texto)
                    except Exception as e: print(f"❌ Cmd error: {e}")
        except Exception as e:
            print(f"⚠️ Hilo: {e}")
        time.sleep(1)

# ═══════════════════════════════════════════════════════════════════════════════
# BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════════════════

def cargar_estado_anterior():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k in ["pedidos_procesados","productos_a","productos_b"]:
                    if k not in data: data[k] = [] if k == "pedidos_procesados" else {}
                if "sincronizados" not in data: data["sincronizados"] = {}
                return data
        except Exception: pass
    return {"productos_a":{},"productos_b":{},"pedidos_procesados":[],"sincronizados":{}}

def guardar_estado_actual(estado):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ DB: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

def limpiar_precio_simple(texto):
    if not texto: return 0
    if "," in texto: texto = texto.split(",")[0]
    numeros = ''.join(filter(str.isdigit, texto))
    return int(numeros) if numeros else 0

def procesar_html_precio(html_precio):
    if not html_precio: return 0, 0, False
    try:
        del_tag = html_precio.find('del'); ins_tag = html_precio.find('ins')
        if del_tag and ins_tag:
            pv = limpiar_precio_simple(del_tag.text); pn = limpiar_precio_simple(ins_tag.text)
            if pn > 0: return pn, pv, True
        return limpiar_precio_simple(html_precio.text), 0, False
    except Exception: return 0, 0, False

def son_coincidentes_inteligentes(nombre1, nombre2):
    n1 = nombre1.lower(); n2 = nombre2.lower()
    if " ".join(n1.split()) == " ".join(n2.split()): return True
    for pr in ['bateria','battery','bat','face','id','maneral','mango','zocalo','board']:
        if (pr in n1) != (pr in n2): return False
    for pc in ['mini','pro','plus','max','kit','ultra','xl','lw-a1']:
        if (pc in n1) != (pc in n2): return False
    stop = {'de','para','con','el','la','los','las','un','una','y','en','del','al'}
    n1c = set(re.sub(r'[^a-z0-9 ]', ' ', n1).split()) - stop
    n2c = set(re.sub(r'[^a-z0-9 ]', ' ', n2).split()) - stop
    if not n1c or not n2c: return False
    nums1 = {w for w in n1c if any(c.isdigit() for c in w)}
    nums2 = {w for w in n2c if any(c.isdigit() for c in w)}
    if (nums1 or nums2) and nums1 != nums2: return False
    return len(n1c & n2c) / min(len(n1c), len(n2c)) >= 0.85

# ═══════════════════════════════════════════════════════════════════════════════
# GMAIL
# ═══════════════════════════════════════════════════════════════════════════════

def extraer_productos_del_mail(cuerpo):
    productos = []; en_bloque = False
    for linea in cuerpo.split('\n'):
        l = linea.strip()
        if "productos:" in l.lower(): en_bloque = True; continue
        if en_bloque:
            if not l or "subtotal:" in l.lower() or "descuento" in l.lower():
                en_bloque = False; continue
            if l.startswith('-'):
                m = re.match(r'-\s*(.+?)\s+x(\d+)\s*-', l)
                if m: productos.append({"nombre": m.group(1).strip(), "cantidad": int(m.group(2))})
    return productos

def chequear_nuevos_pedidos_gmail():
    pedidos = []
    if not GMAIL_USER or not GMAIL_PASS: return pedidos
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_PASS); mail.select("inbox")
        hace_24h = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        status, msgs = mail.search(None, f'(FROM "tiendanegocio.com" SINCE {hace_24h})')
        if status != "OK" or not msgs[0]:
            mail.close(); mail.logout(); return pedidos
        for msg_id in msgs[0].split():
            str_id = msg_id.decode()
            res, data = mail.fetch(msg_id, "(RFC822)")
            if res != "OK": continue
            msg = email.message_from_bytes(data[0][1])
            subject, enc = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes): subject = subject.decode(enc or "utf-8")
            if not any(p in subject.lower() for p in ["compra","realizó","pedido","venta"]): continue
            match = re.search(r'#(\d+)', subject)
            num_orden = match.group(1) if match else str_id
            cuerpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        cuerpo = part.get_payload(decode=True).decode("utf-8", errors="ignore"); break
            else:
                cuerpo = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            items = extraer_productos_del_mail(cuerpo)
            if items: pedidos.append({"id_mail": str_id, "num_orden": num_orden, "productos": items})
        mail.close(); mail.logout()
    except Exception as e: print(f"❌ Gmail: {e}")
    return pedidos

def verificar_pedido_contra_proveedor(pedido, prod_proveedor):
    reporte = f"🛒 *¡Nuevo Pedido! (Orden #{pedido['num_orden']})*\n\n"
    todo_ok = True
    for item in pedido["productos"]:
        nombre = item["nombre"].lower()
        datos_prov = next((d for c, d in prod_proveedor.items() if son_coincidentes_inteligentes(nombre, c)), None)
        if datos_prov:
            reporte += f"✅ *{item['nombre']}* (x{item['cantidad']})\n  Proveedor: CON STOCK a ${datos_prov['precio']:,}\n\n"
        else:
            todo_ok = False
            reporte += f"❌ *{item['nombre']}* (x{item['cantidad']})\n  Proveedor: SIN STOCK\n\n"
    reporte += "🚀 ¡Podés armar el pedido!" if todo_ok else "⚠️ Hay faltantes."
    return reporte

# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPING
# ═══════════════════════════════════════════════════════════════════════════════

def extraer_variaciones_woocommerce(url_producto):
    variaciones = {}
    target = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url_producto}"
    try:
        resp = requests.get(target, timeout=30, verify=False)
        if resp.status_code != 200: return variaciones
        soup = BeautifulSoup(resp.text, 'lxml')
        form = soup.find('form', class_='variations_form')
        if form and form.get('data-product_variations'):
            for var in json.loads(form['data-product_variations']):
                atrs = [str(v).replace('-',' ').replace('_',' ').strip()
                        for k, v in var.get('attributes',{}).items() if v]
                if not atrs: continue
                texto = " - ".join(atrs).title()
                precio = var.get('display_price', 0)
                in_stock = var.get('is_in_stock', True)
                if "agotado" in var.get('variation_html','').lower(): in_stock = False
                if precio > 0 and in_stock:
                    variaciones[texto] = {"precio": int(precio), "stock": True}
    except Exception as e: print(f"⚠️ Variantes: {e}")
    return variaciones

def scrapear_web_a():
    productos = {}
    if not SCRAPERAPI_KEY: return productos
    target = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={URL_A}"
    try:
        print("📥 Scrapeando proveedor...")
        resp = requests.get(target, timeout=60, verify=False)
        if resp.status_code != 200: return productos
        soup = BeautifulSoup(resp.text, 'lxml')
        items = soup.select('.product') or soup.find_all(['li','div'], class_=lambda x: x and 'product' in x)
        for item in items:
            title_el = (item.find(['h2','h3','h4','a','p'],
                         class_=lambda x: x and ('title' in x or 'woocommerce-loop' in x or 'name' in x))
                        or item.find(['h2','h3','h4']))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x))
            link_el  = item.find('a', href=True)
            if not title_el or not title_el.text.strip(): continue
            nombre_original = title_el.text.strip()
            if len(nombre_original) < 4 or nombre_original.lower() == "productos": continue
            nombre_clave = " ".join(nombre_original.lower().split())
            interesa    = any(p in nombre_clave for p in PALABRAS_INTERES)
            es_variable = (item.find('a', class_='product_type_variable') or (price_el and "–" in price_el.text))
            if interesa and es_variable and link_el:
                for nombre_var, datos_var in extraer_variaciones_woocommerce(link_el['href']).items():
                    nc = f"{nombre_original} ({nombre_var})"
                    productos[nc.lower()] = {
                        "nombre_real": nc, "nombre_base_proveedor": nombre_clave,
                        "precio": datos_var["precio"], "precio_anterior": 0,
                        "en_oferta": False, "stock": datos_var["stock"]
                    }
                time.sleep(1.0)
            elif price_el:
                precio, precio_anterior, en_oferta = procesar_html_precio(price_el)
                if precio > 0:
                    productos[nombre_clave] = {
                        "nombre_real": nombre_original, "nombre_base_proveedor": nombre_clave,
                        "precio": precio, "precio_anterior": precio_anterior,
                        "en_oferta": en_oferta, "stock": True
                    }
    except Exception as e: print(f"❌ Scraping A: {e}")
    return productos

def scrapear_web_b():
    productos = {}; pagina = 1
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    while True:
        url = URL_B if pagina == 1 else f"{URL_B}?page={pagina}"
        html = None; en_pagina = 0
        for _ in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                if resp.status_code == 200: html = resp.text; break
                if resp.status_code == 404: break
            except Exception: pass
            time.sleep(2)
        if not html: break
        soup  = BeautifulSoup(html, 'lxml')
        items = soup.find_all(['div','li','article','form'])
        for item in items:
            title_el = item.find(['h2','h3','h1','a'],
                         class_=lambda x: x and ('title' in x or 'name' in x or 'producto' in x))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x or 'money' in x))
            if not title_el or not price_el or not title_el.text.strip(): continue
            nombre_original = title_el.text.strip()
            if len(nombre_original) < 4 or nombre_original.lower() == "productos": continue
            nombre_clave = " ".join(nombre_original.lower().split())
            if nombre_clave not in productos:
                precio, _, _ = procesar_html_precio(price_el)
                texto = item.text.lower()
                tiene_stock = "sin stock" not in texto and "agotado" not in texto
                if precio > 0:
                    productos[nombre_clave] = {"nombre_real": nombre_original,
                        "precio": precio, "stock": tiene_stock}
                    en_pagina += 1
        if en_pagina == 0: break
        pagina += 1; time.sleep(0.5)
    return productos

# ═══════════════════════════════════════════════════════════════════════════════
# LÓGICA PRINCIPAL DE MONITOREO (cada 15 min — solo detecta CAMBIOS)
# ═══════════════════════════════════════════════════════════════════════════════

def procesar_logica():
    print("\n─── 🔄 Nuevo ciclo de monitoreo ───")
    estado_anterior   = cargar_estado_anterior()
    pedidos_proc      = estado_anterior.get("pedidos_procesados", [])
    historial_a_viejo = estado_anterior.get("productos_a", {})
    sincronizados     = estado_anterior.get("sincronizados", {})

    for ped in chequear_nuevos_pedidos_gmail():
        if ped["id_mail"] not in pedidos_proc:
            enviar_telegram(verificar_pedido_contra_proveedor(ped, historial_a_viejo))
            pedidos_proc.append(ped["id_mail"])

    prod_a = scrapear_web_a()
    prod_b = scrapear_web_b()
    if not prod_a:
        print("⚠️ Proveedor 0 productos. Abortando."); return

    historial_consolidado = {**historial_a_viejo, **prod_a}
    bloque_ofertas = bloque_nuevos = ""
    lineas_recuperados = []
    lineas_precios     = []
    lineas_sin_stock   = []

    # ── Analizar proveedor (novedades) ──────────────────────────────────────
    for clave, datos in prod_a.items():
        if not any(p in clave for p in PALABRAS_INTERES): continue
        estaba = clave in historial_a_viejo
        viejo  = historial_a_viejo.get(clave, {})

        # Oferta nueva
        if datos["en_oferta"] and not viejo.get("en_oferta", False):
            bloque_ofertas += f"• *{datos['nombre_real']}*\n  Reg: ${datos['precio_anterior']:,} → 🔥 ${datos['precio']:,}\n\n"

        base_prov    = datos.get("nombre_base_proveedor", clave)
        lo_tengo_web = any(
            son_coincidentes_inteligentes(clave, cb) or son_coincidentes_inteligentes(base_prov, cb)
            for cb in prod_b
        )

        # Producto nuevo en proveedor que no tengo en mi web
        if not lo_tengo_web and (not estaba or viejo.get("precio", 0) == 0):
            precio_ideal = redondear_precio(datos['precio'] / 0.78)
            bloque_nuevos += f"• *{datos['nombre_real']}*\n  Costo: ${datos['precio']:,} → Sugerido: ${precio_ideal:,}\n\n"

        # Proveedor recuperó stock de un producto que estaba sin stock
        elif lo_tengo_web and estaba and not viejo.get("stock", False) and datos["stock"]:
            nombre_real = datos["nombre_real"]
            sync_actual = sincronizados.get(nombre_real, {})
            if sync_actual.get("sin_stock", False):
                # Solo actuar si estaba marcado como sin stock
                if _api_token:
                    product_id, variant_id = buscar_producto_api(nombre_real)
                    if product_id and variant_id:
                        if restaurar_stock_libre_api(variant_id):
                            sincronizados.pop(nombre_real, None)
                            lineas_recuperados.append(
                                f"• *{nombre_real}*\n  Proveedor vuelve a tener stock a ${datos['precio']:,}"
                            )
                else:
                    sincronizados.pop(nombre_real, None)
                    lineas_recuperados.append(
                        f"• *{nombre_real}*\n  Proveedor vuelve a tener stock a ${datos['precio']:,}"
                    )

    # ── Analizar mi tienda contra proveedor (cambios de precio y stock) ─────
    for clave_b, datos_b in prod_b.items():
        datos_a = None
        for clave_a, da in prod_a.items():
            if son_coincidentes_inteligentes(clave_b, clave_a):
                datos_a = da; break
        if datos_a is None:
            variantes = [d for c, d in prod_a.items()
                         if son_coincidentes_inteligentes(clave_b, d.get("nombre_base_proveedor", ""))]
            if variantes: datos_a = min(variantes, key=lambda x: x["precio"])

        if not datos_b["stock"]: continue

        nombre_real = datos_b["nombre_real"]
        sync_actual = sincronizados.get(nombre_real, {})

        if datos_a is None:
            # Proveedor sin stock → marcar sin stock (solo si no lo hicimos ya)
            if not sync_actual.get("sin_stock", False):
                if _api_token:
                    product_id, variant_id = buscar_producto_api(nombre_real)
                    if product_id and variant_id:
                        if modificar_stock_api(variant_id, 0):
                            sincronizados[nombre_real] = {"sin_stock": True}
                            lineas_sin_stock.append(f"• *{nombre_real}*")
                else:
                    sincronizados[nombre_real] = {"sin_stock": True}
                    lineas_sin_stock.append(f"• *{nombre_real}* _(sin API)_")
        else:
            # Proveedor tiene stock → limpiar flag sin_stock si existía
            if sync_actual.get("sin_stock", False):
                sincronizados.pop(nombre_real, None)

            # Actualizar precio solo si cambió respecto al último que sincronizamos
            precio_objetivo    = redondear_precio(datos_a["precio"] / 0.78)
            ultimo_precio_sync = sync_actual.get("precio")

            if precio_objetivo != ultimo_precio_sync:
                if _api_token:
                    product_id, variant_id = buscar_producto_api(nombre_real)
                    if product_id and variant_id:
                        if modificar_precio_api(variant_id, precio_objetivo):
                            sincronizados[nombre_real] = {"precio": precio_objetivo}
                            if datos_b["precio"] != precio_objetivo:
                                lineas_precios.append(
                                    f"• *{nombre_real}*\n"
                                    f"  Proveedor: ${datos_a['precio']:,} | "
                                    f"Anterior: ${datos_b['precio']:,} | "
                                    f"*Nuevo: ${precio_objetivo:,}*"
                                )
                else:
                    sincronizados[nombre_real] = {"precio": precio_objetivo}
                    if datos_b["precio"] != precio_objetivo:
                        lineas_precios.append(
                            f"• *{nombre_real}*\n"
                            f"  Proveedor: ${datos_a['precio']:,} | "
                            f"Anterior: ${datos_b['precio']:,} | "
                            f"*Sugerido: ${precio_objetivo:,}*"
                        )

    # ── Enviar mensajes ─────────────────────────────────────────────────────
    if bloque_ofertas:
        enviar_telegram(f"🏷️ *¡Descuentos en el Proveedor!*\n\n{bloque_ofertas}")
    if bloque_nuevos:
        enviar_telegram(f"🔥 *¡Nuevo producto en el Proveedor!*\n\n{bloque_nuevos}")
    if lineas_recuperados:
        enviar_telegram("🔄 *¡Stock recuperado en Proveedor!*\n\n" + "\n\n".join(lineas_recuperados))
    if lineas_precios:
        for i in range(0, len(lineas_precios), 20):
            accion = "🤖 *Precios actualizados:*" if _api_token else "📉 *Precios a actualizar:*"
            enviar_telegram(f"{accion}\n\n" + "\n\n".join(lineas_precios[i:i+20]))
    if lineas_sin_stock:
        for i in range(0, len(lineas_sin_stock), 30):
            accion = "🤖 *Marcados sin stock:*" if _api_token else "⚠️ *Sin stock en proveedor:*"
            enviar_telegram(f"{accion}\n\n" + "\n".join(lineas_sin_stock[i:i+30]))

    if not any([bloque_ofertas, bloque_nuevos, lineas_recuperados, lineas_precios, lineas_sin_stock]):
        print("✅ Sin cambios detectados.")

    guardar_estado_actual({
        "productos_a":        historial_consolidado,
        "productos_b":        prod_b,
        "pedidos_procesados": pedidos_proc,
        "sincronizados":      sincronizados,
        "api_token":          _api_token,
        "api_user_id":        _api_user_id
    })
    print("─── ✅ Ciclo completado ───")

# ═══════════════════════════════════════════════════════════════════════════════
# ARRANQUE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 Bot de Dropshipping iniciado...")
    _cargar_token_desde_db()

    if _api_token:
        enviar_telegram(
            f"🟢 *Bot iniciado* — Token activo (store_id: `{_api_user_id}`)\n\n"
            f"Mandá `/sync_total` para sincronizar todos los precios y stock ahora.\n"
            f"Mandá `/ayuda` para ver todos los comandos."
        )
    else:
        enviar_telegram(
            "🟡 *Bot iniciado* — Sin token API.\n"
            "Mandá `/ayuda` para ver los comandos."
        )

    hilo = threading.Thread(target=bucle_escucha_telegram, daemon=True)
    hilo.start()

    # Primer ciclo para poblar los datos
    procesar_logica()

    print("💤 Entrando en ciclo de 15 minutos...")
    while True:
        time.sleep(900)
        procesar_logica()
