"""
Bot de Dropshipping — Tienda Negocio
Compara precios del proveedor (rxzweb.com) con la tienda propia
y sincroniza precios/stock automáticamente via API.
"""
import os, time, json, re, io, threading, imaplib, email
from email.header import decode_header
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
DB_FILE        = "estado_productos.json"
URL_PROVEEDOR  = "https://rxzweb.com/tienda/?et_per_page=-1"
API_BASE       = "https://developers.tiendanegocio.com/v1"
USER_AGENT     = "dropshipping (lean.6roid@gmail.com)"
# MARGEN: porcentaje que el precio de venta cubre sobre el costo
# 0.78 = ganancia neta 22% | 0.90 = ganancia neta 10%
# Configurable por variable de entorno MARGEN en Railway
MARGEN = float(os.environ.get("MARGEN", "0.78"))

PALABRAS_INTERES = [
    'ma ant','amaoe','2uul','goot wick','mijing','louwei','rf4','jakemy',
    'kailiwei','kslid','aifen','sugon','jcid','jc','v1','v1s','v1se',
    'v1 pro','programadora','organizador','cinta','silla','mesa','puas',
    'hilo','cepillo'
]

# ── Variables de entorno ──────────────────────────────────────────────────────
def _e(nombre):
    """Lee variable de entorno por nombre exacto."""
    return (os.environ.get(nombre) or "").strip() or None

TELEGRAM_TOKEN = _e("TELEGRAM_TOKEN")
CHAT_ID        = _e("CHAT_ID")
SCRAPERAPI_KEY = _e("SCRAPERAPI_KEY")
GMAIL_USER     = _e("GMAIL_USER")
GMAIL_PASS     = _e("GMAIL_PASS")
CLIENT_ID      = _e("CLIENT_ID")
CLIENT_SECRET  = _e("CLIENT_SECRET")
NOMBRE_TIENDA  = _e("NOMBRE_TIENDA") or "🧪 PRUEBA"

# ── Token API (estado global) ─────────────────────────────────────────────────
_token    = None
_store_id = None

def cargar_token():
    global _token, _store_id
    t = _e("API_TOKEN")
    u = _e("API_USER_ID")
    if t and u:
        _token = t; _store_id = u
        print(f"✅ Token desde Railway (store_id={u})")
        return
    db = leer_db()
    if db.get("api_token") and db.get("api_user_id"):
        _token    = db["api_token"]
        _store_id = db["api_user_id"]
        print(f"✅ Token desde DB (store_id={_store_id})")

def guardar_token(token, store_id):
    global _token, _store_id
    _token = token; _store_id = store_id
    db = leer_db()
    db["api_token"]   = token
    db["api_user_id"] = store_id
    escribir_db(db)
    print(f"💾 Token guardado (store_id={store_id})")

# ══════════════════════════════════════════════════════════════════════════════
# BASE DE DATOS LOCAL (JSON)
# ══════════════════════════════════════════════════════════════════════════════
def leer_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("productos_proveedor", {})
                data.setdefault("sincronizados", {})
                data.setdefault("pedidos_procesados", [])
                return data
        except Exception:
            pass
    return {"productos_proveedor": {}, "sincronizados": {}, "pedidos_procesados": []}

def escribir_db(data):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ DB write: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════
def tg(msg):
    if not msg or not TELEGRAM_TOKEN or not CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=15
        )
    except Exception as e:
        print(f"❌ Telegram: {e}")

def tg_archivo(data_bytes, nombre, caption=""):
    if not TELEGRAM_TOKEN or not CHAT_ID: return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"document": (nombre, data_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=30
        )
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Telegram archivo: {e}"); return False

# ══════════════════════════════════════════════════════════════════════════════
# API — TIENDA NEGOCIO
# ══════════════════════════════════════════════════════════════════════════════
def _headers():
    return {
        "Authorization": f"Bearer {_token}",
        "User-Agent":    USER_AGENT,
        "Content-Type":  "application/json"
    }

def _get(url, params=None):
    for _ in range(3):
        try:
            r = requests.get(url, headers=_headers(), params=params, timeout=20)
            if r.status_code == 429: time.sleep(3); continue
            return r
        except Exception as e:
            print(f"❌ GET {url}: {e}"); time.sleep(1)
    return None

def _put(url, data):
    for _ in range(3):
        try:
            r = requests.put(url, headers=_headers(), json=data, timeout=15)
            if r.status_code == 429: time.sleep(3); continue
            return r
        except Exception as e:
            print(f"❌ PUT {url}: {e}"); time.sleep(1)
    return None

def _post_api(url, data):
    for _ in range(3):
        try:
            r = requests.post(url, headers=_headers(), json=data, timeout=15)
            if r.status_code == 429: time.sleep(3); continue
            return r
        except Exception as e:
            print(f"❌ POST {url}: {e}"); time.sleep(1)
    return None

# ── Catálogo de mi tienda ─────────────────────────────────────────────────────
# Guardamos como lista para no perder productos con nombres similares
_catalogo_cache     = None
_catalogo_cache_ts  = 0

def obtener_catalogo(forzar=False):
    """
    Devuelve lista de dicts con todos los productos de la tienda.
    Cada producto tiene:
      id, nombre, nombre_norm, precio_base,
      tiene_variantes, variantes:[{id, nombre_variante, precio}], published
    """
    global _catalogo_cache, _catalogo_cache_ts
    if not forzar and _catalogo_cache and (time.time() - _catalogo_cache_ts) < 300:
        return _catalogo_cache
    if not _token:
        return []

    # Paso 1: todos los productos (sin variantes para poder traer 297)
    print("📥 API Paso 1: productos...")
    productos_raw = []
    pagina = 1
    while True:
        r = _get(f"{API_BASE}/products", params={"per_page": 200, "page": pagina})
        if not r or r.status_code != 200:
            print(f"❌ API productos HTTP {r.status_code if r else 'None'}"); break
        data = r.json()
        lote = data.get("results", data) if isinstance(data, dict) else data
        if not lote: break
        productos_raw.extend(lote)
        print(f"   Página {pagina}: {len(lote)} items (total: {len(productos_raw)})")
        has_next = data.get("pagination", {}).get("next_page") if isinstance(data, dict) else None
        if not has_next: break
        pagina += 1
        time.sleep(0.3)

    print(f"   ✅ {len(productos_raw)} productos encontrados")

    # Paso 2: variantes de cada producto
    print("📥 API Paso 2: variantes...")
    catalogo = []
    for i, p in enumerate(productos_raw):
        pid   = p.get("id")
        nombre_raw = p.get("name", {})
        if isinstance(nombre_raw, dict):
            nombre = nombre_raw.get("es") or nombre_raw.get("en") or next(iter(nombre_raw.values()), "")
        else:
            nombre = str(nombre_raw or "")
        nombre = nombre.strip()
        if not nombre or not pid:
            continue

        nombre_norm = normalizar(nombre)

        # Traer variantes
        r_var = _get(f"{API_BASE}/products/{pid}/variants", params={"per_page": 200})
        variantes = []
        if r_var and r_var.status_code == 200:
            data_var = r_var.json()
            lista_var = data_var.get("results", data_var) if isinstance(data_var, dict) else data_var
            if isinstance(lista_var, list):
                for v in lista_var:
                    vid = v.get("id")
                    if not vid: continue
                    # Nombre de la variante (campo values[].es o values[].en)
                    values = v.get("values", [])
                    nombre_var = " ".join(
                        str(val.get("es") or val.get("en") or "").strip()
                        for val in values
                    ).strip()
                    variantes.append({
                        "id":     vid,
                        "nombre": nombre_var,
                        "precio": float(v.get("price", 0) or 0)
                    })

        precio_base = variantes[0]["precio"] if variantes else float(p.get("price", 0) or 0)

        catalogo.append({
            "id":              pid,
            "nombre":          nombre,
            "nombre_norm":     nombre_norm,
            "precio_base":     precio_base,
            "tiene_variantes": len(variantes) > 0,
            "variantes":       variantes,
            "published":       p.get("published", True)
        })

        if (i + 1) % 50 == 0:
            print(f"   Variantes: [{i+1}/{len(productos_raw)}]")
        time.sleep(0.4)

    print(f"   ✅ Catálogo listo: {len(catalogo)} productos con datos")
    _catalogo_cache    = catalogo
    _catalogo_cache_ts = time.time()
    return catalogo

# ── Actualizar precio ─────────────────────────────────────────────────────────
def set_precio_variante(variant_id, precio):
    r = _put(f"{API_BASE}/variants/{variant_id}", {"price": str(int(precio))})
    ok = r and r.status_code in (200, 201)
    if not ok: print(f"  ⚠️ variant {variant_id} precio HTTP {r.status_code if r else 'None'}")
    return ok

def set_precio_producto(product_id, precio):
    """Para productos SIN variantes: actualiza precio directo en el producto."""
    r = _put(f"{API_BASE}/products/{product_id}", {"price": str(int(precio))})
    ok = r and r.status_code in (200, 201)
    if not ok: print(f"  ⚠️ product {product_id} precio HTTP {r.status_code if r else 'None'}")
    return ok

def set_stock_variante(variant_id, stock):
    r = _put(f"{API_BASE}/variants/{variant_id}", {"stock": stock, "stock_management": True})
    ok = r and r.status_code in (200, 201)
    return ok

def set_visibilidad(product_id, published):
    r = _put(f"{API_BASE}/products/{product_id}", {"published": published})
    return r and r.status_code in (200, 201)

# ══════════════════════════════════════════════════════════════════════════════
# OAUTH
# ══════════════════════════════════════════════════════════════════════════════
def canjear_code(auth_code):
    payload = {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type":    "authorization_code",
        "code":          auth_code
    }
    try:
        r = requests.post(f"{API_BASE}/oauth/app/token", json=payload,
                          headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                          timeout=30)
        print(f"OAuth HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code in (200, 201):
            d       = r.json().get("data", r.json())
            token   = d.get("access_token")
            user_id = str(d.get("store_id") or d.get("user_id") or "")
            if token and user_id:
                guardar_token(token, user_id)
                return token
    except Exception as e:
        print(f"❌ OAuth: {e}")
    return None

# ══════════════════════════════════════════════════════════════════════════════
# MATCHING DE NOMBRES
# ══════════════════════════════════════════════════════════════════════════════
STOP = {'de','para','con','el','la','los','las','un','una','y','en','del','al','a','o','e'}

def normalizar(nombre):
    return " ".join(re.sub(r'[^a-z0-9 ]', ' ', str(nombre).lower()).split())

def palabras(nombre_norm):
    return set(nombre_norm.split()) - STOP

def match(n1, n2):
    """
    Devuelve True si n1 y n2 refieren al mismo producto.
    n1 y n2 deben estar ya normalizados.
    """
    if n1 == n2: return True
    w1 = palabras(n1); w2 = palabras(n2)
    if not w1 or not w2: return False

    # Palabras críticas: presencia distinta = productos distintos
    criticas = {'bateria','battery','bat','face','maneral','mango','zocalo',
                'board','mini','plus','max','kit','ultra','xl'}
    for c in criticas:
        if (c in w1) != (c in w2): return False

    # Números: los del nombre más corto deben estar en el más largo
    nums1 = {w for w in w1 if any(c.isdigit() for c in w)}
    nums2 = {w for w in w2 if any(c.isdigit() for c in w)}
    if nums1 and nums2:
        corto = nums1 if len(w1) <= len(w2) else nums2
        largo = nums2 if len(w1) <= len(w2) else nums1
        if not corto.issubset(largo): return False

    # Overlap ≥ 75% del nombre más corto
    comunes = w1 & w2
    return len(comunes) / min(len(w1), len(w2)) >= 0.75

# ══════════════════════════════════════════════════════════════════════════════
# PRECIO OBJETIVO
# ══════════════════════════════════════════════════════════════════════════════
def precio_objetivo(costo):
    """Calcula el precio de venta para obtener MARGEN% de ganancia neta."""
    raw = costo / MARGEN
    if raw >= 100000: return round(raw / 1000) * 1000
    if raw >= 10000:  return round(raw / 500)  * 500
    if raw >= 1000:   return round(raw / 100)  * 100
    return round(raw / 50) * 50

# ══════════════════════════════════════════════════════════════════════════════
# SINCRONIZACIÓN
# ══════════════════════════════════════════════════════════════════════════════
def sincronizar_producto(producto_api, prod_prov_base, prod_prov_variantes):
    """
    Actualiza precios de un producto de mi tienda contra el proveedor.

    prod_prov_base:      precio del producto base en el proveedor (o None)
    prod_prov_variantes: dict {nombre_variante_norm: precio} del proveedor

    Devuelve (ok, precio_min, precio_max) o (False, 0, 0)
    """
    pid       = producto_api["id"]
    variantes = producto_api["variantes"]

    if not variantes:
        # Producto sin variantes → precio directo
        if prod_prov_base is None:
            return False, 0, 0
        p = precio_objetivo(prod_prov_base)
        ok = set_precio_producto(pid, p)
        time.sleep(0.4)
        return ok, p, p

    # Producto con variantes
    precios_asignados = {}
    for var in variantes:
        costo = None
        # 1. Intentar match por nombre de variante
        var_norm = normalizar(var["nombre"])
        for pv_norm, pv_precio in prod_prov_variantes.items():
            if match(var_norm, pv_norm):
                costo = pv_precio
                break
        # 2. Fallback: precio base del proveedor
        if costo is None and prod_prov_base is not None:
            costo = prod_prov_base
        if costo is None:
            continue
        precios_asignados[var["id"]] = precio_objetivo(costo)

    if not precios_asignados:
        return False, 0, 0

    exitos = 0
    for vid, p in precios_asignados.items():
        if set_precio_variante(vid, p):
            exitos += 1
        time.sleep(0.4)

    if exitos == 0:
        return False, 0, 0

    precio_min = min(precios_asignados.values())
    precio_max = max(precios_asignados.values())
    return True, precio_min, precio_max

def marcar_sin_stock(producto_api):
    """
    Marca el producto como sin stock SIN ocultarlo.
    - Con variantes: stock=0 en cada variante (stock_management=True)
    - Sin variantes: intenta stock=0 directo en el producto
    NUNCA oculta el producto (published=False).
    """
    pid       = producto_api["id"]
    variantes = producto_api["variantes"]
    if variantes:
        ok = True
        for var in variantes:
            if not set_stock_variante(var["id"], 0): ok = False
            time.sleep(0.4)
        return ok
    else:
        # Sin variantes: intentar poner stock=0 en el producto
        r = _put(f"{API_BASE}/products/{pid}", {"stock": 0, "stock_management": True})
        ok = r and r.status_code in (200, 201)
        if not ok:
            # Si no acepta stock directo, al menos dejarlo visible (no hacer nada)
            print(f"⚠️ No pude marcar sin stock producto {pid} — se deja visible")
            return True  # True para evitar spam, no es un error crítico
        return ok

# ══════════════════════════════════════════════════════════════════════════════
# SYNC TOTAL (comando manual)
# ══════════════════════════════════════════════════════════════════════════════
def run_sync_total():
    """Sincroniza TODOS los productos de mi tienda contra el proveedor."""
    db        = leer_db()
    prov      = db.get("productos_proveedor", {})
    sinc      = db.get("sincronizados", {})

    if not prov:
        tg("⚠️ Sin datos del proveedor. Esperá que complete un ciclo de monitoreo primero.")
        return

    catalogo = obtener_catalogo(forzar=True)
    if not catalogo:
        tg("❌ No pude obtener el catálogo de la API.")
        return

    # Construir índice del proveedor: base_norm → {variantes: {norm: precio}, precio_base}
    idx_prov = _construir_indice(prov)

    total          = len(catalogo)
    act_precios    = []
    act_sin_stock  = []
    sin_match      = []
    errores        = []

    tg(f"🔄 *[{NOMBRE_TIENDA}] Sync total iniciada*\n{total} productos a procesar...")

    for i, prod in enumerate(catalogo):
        nombre_norm = prod["nombre_norm"]
        nombre_real = prod["nombre"]
        precio_web  = prod["precio_base"]

        # Buscar en proveedor
        base_norm, datos_prov = _buscar_en_indice(nombre_norm, idx_prov)

        if datos_prov is None:
            # No está en el proveedor → sin stock
            ok = marcar_sin_stock(prod)
            if ok:
                sinc[nombre_real] = {"sin_stock": True}
                act_sin_stock.append(f"• *{nombre_real}*")
            else:
                errores.append(nombre_real)
            continue

        # Está en el proveedor → calcular precio
        p_obj       = precio_objetivo(datos_prov["precio_base"])
        ultimo_sinc = sinc.get(nombre_real, {}).get("precio", 0)

        ok, p_min, p_max = sincronizar_producto(
            prod,
            datos_prov["precio_base"],
            datos_prov["variantes"]
        )

        if ok:
            sinc[nombre_real] = {"precio": p_min, "ts": time.time()}
            if p_min != ultimo_sinc:  # Solo reportar si realmente cambió
                rango = f"${p_min:,}" if p_min == p_max else f"${p_min:,}–${p_max:,}"
                act_precios.append(
                    f"• *{nombre_real}*\n"
                    f"  Antes: ${int(precio_web):,} → *Nuevo: {rango}*"
                )
        else:
            errores.append(nombre_real)

        if (i + 1) % 50 == 0:
            print(f"   Sync [{i+1}/{total}]")

    # Guardar y notificar
    db["sincronizados"] = sinc
    escribir_db(db)

    resumen = (
        f"✅ *[{NOMBRE_TIENDA}] Sync total terminada*\n\n"
        f"📊 Total: *{total}*\n"
        f"💲 Precios actualizados: *{len(act_precios)}*\n"
        f"📦 Sin stock: *{len(act_sin_stock)}*\n"
        f"❓ Sin match: *{len(sin_match)}*\n"
    )
    if errores: resumen += f"⚠️ Errores: *{len(errores)}*"
    tg(resumen)

    for i in range(0, len(act_precios), 20):
        tg(f"💲 *Precios actualizados:*\n\n" + "\n\n".join(act_precios[i:i+20]))
    for i in range(0, len(act_sin_stock), 30):
        tg(f"📦 *Marcados sin stock:*\n\n" + "\n".join(act_sin_stock[i:i+30]))

# ── Helpers del índice ────────────────────────────────────────────────────────
def _construir_indice(prov):
    """
    Construye índice: {base_norm: {"precio_base": X, "variantes": {var_norm: precio}}}
    El proveedor guarda entradas como:
      - Simple:      "microscopio rf4 rf-6558x" → precio
      - Con variante: "jc face id flex... (13 13mini...)" → precio
    """
    idx = {}
    for clave, datos in prov.items():
        base_raw = datos.get("nombre_base_proveedor", clave)
        base_norm = normalizar(base_raw)
        precio    = datos["precio"]

        if base_norm not in idx:
            idx[base_norm] = {"precio_base": precio, "variantes": {}}

        # Si la clave tiene paréntesis, extraer la variante
        if '(' in clave:
            var_part = clave.split('(', 1)[-1].rstrip(')')
            var_norm = normalizar(var_part)
            idx[base_norm]["variantes"][var_norm] = precio
        else:
            # Actualizar precio_base con el más bajo disponible
            if precio < idx[base_norm]["precio_base"]:
                idx[base_norm]["precio_base"] = precio

    return idx

def _buscar_en_indice(nombre_norm, idx):
    """Busca nombre_norm en el índice del proveedor. Devuelve (base_norm, datos) o (None, None)."""
    for base_norm, datos in idx.items():
        if match(nombre_norm, base_norm):
            return base_norm, datos
    return None, None

# ══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ══════════════════════════════════════════════════════════════════════════════
def generar_excel():
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return None, "❌ Falta openpyxl en requirements.txt"

    db  = leer_db()
    prov = db.get("productos_proveedor", {})
    if not prov: return None, "❌ Sin datos del proveedor."

    catalogo = obtener_catalogo()
    if not catalogo: return None, "❌ Sin datos de la API."

    idx_prov = _construir_indice(prov)
    cols = [
        "Hash","Nombre del producto","Precio","Oferta","Stock",
        "Visibilidad (Visible o Oculto)","Descripción","SKU",
        "Peso en KG","Alto en CM","Ancho en CM","Profundidad en CM",
        "Nombre de variante #1","Opción de variante #1",
        "Nombre de variante #2","Opción de variante #2",
        "Nombre de variante #3","Opción de variante #3",
        "Categorías > Subcategorías > … > Subcategorías"
    ]
    filas = []; act = ign = sin = 0

    for prod in catalogo:
        _, datos_prov = _buscar_en_indice(prod["nombre_norm"], idx_prov)
        if datos_prov is None:
            sin += 1; continue
        p = precio_objetivo(datos_prov["precio_base"])
        if p == int(prod["precio_base"]):
            ign += 1; continue
        fila = {c: "" for c in cols}
        fila["Hash"]               = prod["nombre_norm"]
        fila["Nombre del producto"] = prod["nombre"]
        fila["Precio"]             = p
        filas.append(fila); act += 1

    if not filas:
        return None, f"ℹ️ Sin cambios.\n• {ign} ya correctos\n• {sin} sin match"

    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Productos"
    ws.append(cols)
    hf   = PatternFill("solid", fgColor="1F6B3B")
    hfnt = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    for c in ws[1]:
        c.fill = hf; c.font = hfnt
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 25
    fills  = [PatternFill("solid",fgColor="F0F7F2"), PatternFill("solid",fgColor="FFFFFF")]
    fn     = Font(name="Arial", size=9)
    fv     = Font(name="Arial", size=9, bold=True, color="1F6B3B")
    for i, fila in enumerate(filas, 2):
        ws.append([fila[c] for c in cols])
        for cell in ws[i]: cell.fill = fills[i%2]; cell.font = fn
        ws.cell(row=i,column=3).font = fv
        ws.cell(row=i,column=3).number_format = '#,##0'
    for col,w in zip("ABCDEFGHIJKLMNOPQRS",[38,48,12,8,8,12,8,8,8,8,8,8,20,22,20,22,20,22,30]):
        ws.column_dimensions[col].width = w

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.getvalue(), (
        f"✅ Excel listo:\n• *{act}* a actualizar\n"
        f"• *{ign}* ya correctos\n• *{sin}* sin match\n\n"
        f"Importalo: *Productos → Importar y exportar → Importar*"
    )

# ══════════════════════════════════════════════════════════════════════════════
# SCRAPING DEL PROVEEDOR
# ══════════════════════════════════════════════════════════════════════════════
def _precio_html(el):
    if not el: return 0, 0, False
    try:
        d = el.find('del'); i = el.find('ins')
        if d and i:
            pv = int(''.join(filter(str.isdigit, d.text.split(',')[0])) or 0)
            pn = int(''.join(filter(str.isdigit, i.text.split(',')[0])) or 0)
            if pn > 0: return pn, pv, True
        raw = int(''.join(filter(str.isdigit, el.text.split(',')[0])) or 0)
        return raw, 0, False
    except Exception: return 0, 0, False

def _variantes_woo(url):
    variaciones = {}
    target = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}"
    try:
        r = requests.get(target, timeout=30, verify=False)
        if r.status_code != 200: return variaciones
        form = BeautifulSoup(r.text, 'lxml').find('form', class_='variations_form')
        if form and form.get('data-product_variations'):
            for var in json.loads(form['data-product_variations']):
                atrs = [str(v).replace('-',' ').replace('_',' ').strip()
                        for k,v in var.get('attributes',{}).items() if v]
                if not atrs: continue
                nombre = " - ".join(atrs).title()
                precio = var.get('display_price', 0)
                stock  = var.get('is_in_stock', True)
                if "agotado" in var.get('variation_html','').lower(): stock = False
                if precio > 0 and stock:
                    variaciones[nombre] = {"precio": int(precio)}
    except Exception as e: print(f"⚠️ Variantes woo: {e}")
    return variaciones

def scrapear_proveedor():
    if not SCRAPERAPI_KEY: return {}
    target = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={URL_PROVEEDOR}"
    productos = {}
    try:
        print("📥 Scrapeando proveedor...")
        r = requests.get(target, timeout=60, verify=False)
        if r.status_code != 200: return {}
        soup  = BeautifulSoup(r.text, 'lxml')
        items = soup.select('.product') or soup.find_all(['li','div'],
                    class_=lambda x: x and 'product' in x)
        for item in items:
            title = (item.find(['h2','h3','h4','a','p'],
                        class_=lambda x: x and ('title' in x or 'woocommerce-loop' in x or 'name' in x))
                     or item.find(['h2','h3','h4']))
            price = item.find(class_=lambda x: x and ('price' in x or 'precio' in x))
            link  = item.find('a', href=True)
            if not title or not title.text.strip(): continue
            nombre_orig = title.text.strip()
            if len(nombre_orig) < 4 or nombre_orig.lower() == "productos": continue
            nombre_base = normalizar(nombre_orig)
            interesa    = any(p in nombre_base for p in PALABRAS_INTERES)
            es_variable = (item.find('a', class_='product_type_variable')
                           or (price and "–" in price.text))
            if interesa and es_variable and link:
                vars_woo = _variantes_woo(link['href'])
                for nombre_var, datos_var in vars_woo.items():
                    clave = f"{nombre_orig} ({nombre_var})"
                    productos[clave.lower()] = {
                        "nombre_real":            clave,
                        "nombre_base_proveedor":  nombre_base,
                        "precio":                 datos_var["precio"],
                        "precio_anterior":        0,
                        "en_oferta":              False,
                        "stock":                  True
                    }
                time.sleep(1)
            elif price:
                p, pa, oferta = _precio_html(price)
                if p > 0:
                    productos[nombre_base] = {
                        "nombre_real":           nombre_orig,
                        "nombre_base_proveedor": nombre_base,
                        "precio":                p,
                        "precio_anterior":       pa,
                        "en_oferta":             oferta,
                        "stock":                 True
                    }
    except Exception as e: print(f"❌ Scraping: {e}")
    print(f"   🏪 {len(productos)} productos del proveedor")
    return productos

# ══════════════════════════════════════════════════════════════════════════════
# GMAIL
# ══════════════════════════════════════════════════════════════════════════════
def chequear_gmail():
    if not GMAIL_USER or not GMAIL_PASS: return []
    pedidos = []
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com")
        m.login(GMAIL_USER, GMAIL_PASS); m.select("inbox")
        hace_24h = (datetime.now()-timedelta(days=1)).strftime("%d-%b-%Y")
        st, msgs = m.search(None, f'(FROM "tiendanegocio.com" SINCE {hace_24h})')
        if st != "OK" or not msgs[0]:
            m.close(); m.logout(); return []
        for mid in msgs[0].split():
            res, data = m.fetch(mid, "(RFC822)")
            if res != "OK": continue
            msg = email.message_from_bytes(data[0][1])
            subj, enc = decode_header(msg["Subject"])[0]
            if isinstance(subj, bytes): subj = subj.decode(enc or "utf-8")
            if not any(p in subj.lower() for p in ["compra","realizó","pedido","venta"]): continue
            num = (re.search(r'#(\d+)', subj) or [None, mid.decode()])[1]
            cuerpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        cuerpo = part.get_payload(decode=True).decode("utf-8","ignore"); break
            else:
                cuerpo = msg.get_payload(decode=True).decode("utf-8","ignore")
            items = _parsear_items_pedido(cuerpo)
            if items: pedidos.append({"id": mid.decode(), "num": num, "items": items})
        m.close(); m.logout()
    except Exception as e: print(f"❌ Gmail: {e}")
    return pedidos

def _parsear_items_pedido(cuerpo):
    items = []; en = False
    for linea in cuerpo.split('\n'):
        l = linea.strip()
        if "productos:" in l.lower(): en = True; continue
        if en:
            if not l or "subtotal:" in l.lower(): en = False; continue
            if l.startswith('-'):
                m = re.match(r'-\s*(.+?)\s+x(\d+)\s*-', l)
                if m: items.append({"nombre": m.group(1), "cant": int(m.group(2))})
    return items

# ══════════════════════════════════════════════════════════════════════════════
# CICLO DE MONITOREO (cada 15 min)
# ══════════════════════════════════════════════════════════════════════════════
def ciclo_monitoreo():
    print(f"\n─── 🔄 Ciclo {datetime.now().strftime('%H:%M')} ───")
    db            = leer_db()
    prov_anterior = db.get("productos_proveedor", {})
    sinc          = db.get("sincronizados", {})
    pedidos_proc  = db.get("pedidos_procesados", [])

    # Chequear pedidos Gmail
    for ped in chequear_gmail():
        if ped["id"] not in pedidos_proc:
            msg = f"🛒 *[{NOMBRE_TIENDA}] ¡Nuevo Pedido #{ped['num']}!*\n\n"
            for item in ped["items"]:
                en_prov = any(match(normalizar(item["nombre"]), normalizar(da["nombre_real"]))
                              for da in prov_anterior.values())
                msg += f"{'✅' if en_prov else '❌'} *{item['nombre']}* x{item['cant']}\n"
            tg(msg)
            pedidos_proc.append(ped["id"])

    # Scrapear proveedor
    prov_nuevo = scrapear_proveedor()
    if not prov_nuevo:
        print("⚠️ Proveedor 0 productos. Abortando ciclo.")
        return

    prov_consolidado = {**prov_anterior, **prov_nuevo}

    # Obtener catálogo de mi tienda (usa caché de 5 min)
    catalogo = obtener_catalogo() if _token else []

    idx_prov = _construir_indice(prov_nuevo)

    # ── Detectar novedades del proveedor ──────────────────────────────────────
    bloque_ofertas = bloque_nuevos = bloque_recuperados = ""
    lineas_precios    = []
    lineas_sin_stock  = []

    for clave, datos in prov_nuevo.items():
        if not any(p in clave for p in PALABRAS_INTERES): continue
        viejo = prov_anterior.get(clave, {})

        # Oferta nueva
        if datos["en_oferta"] and not viejo.get("en_oferta", False):
            bloque_ofertas += (f"• *{datos['nombre_real']}*\n"
                               f"  Reg: ${datos['precio_anterior']:,} → 🔥 ${datos['precio']:,}\n\n")

        # Producto nuevo que no tengo en mi tienda
        base_norm = datos.get("nombre_base_proveedor", clave)
        tengo = any(match(p["nombre_norm"], base_norm) for p in catalogo) if catalogo else False
        if not tengo and not viejo:
            p_sugerido = precio_objetivo(datos["precio"])
            bloque_nuevos += (f"• *{datos['nombre_real']}*\n"
                              f"  Costo: ${datos['precio']:,} → Sugerido: ${p_sugerido:,}\n\n")

        # Stock recuperado
        if viejo and not viejo.get("stock", True) and datos.get("stock", True):
            nombre_real = datos["nombre_real"]
            if sinc.get(nombre_real, {}).get("sin_stock"):
                sinc.pop(nombre_real, None)
                bloque_recuperados += f"• *{nombre_real}*\n  Proveedor recuperó stock\n\n"

    # ── Actualizar precios y stock en mi tienda ───────────────────────────────
    for prod in catalogo:
        nombre_real = prod["nombre"]
        nombre_norm = prod["nombre_norm"]
        precio_web  = prod["precio_base"]
        sync_actual = sinc.get(nombre_real, {})

        _, datos_prov = _buscar_en_indice(nombre_norm, idx_prov)

        if datos_prov is None:
            # Proveedor no tiene → marcar sin stock UNA SOLA VEZ
            # Se resetea solo cuando el proveedor vuelva a tener stock
            if not sync_actual.get("sin_stock", False):
                if _token and marcar_sin_stock(prod):
                    sinc[nombre_real] = {"sin_stock": True}
                    etiqueta = f"({len(prod['variantes'])} var.)" if prod["tiene_variantes"] else "(sin var.)"
                    lineas_sin_stock.append(f"• *{nombre_real}* {etiqueta}")
        else:
            # Limpiar flag sin_stock si volvió
            if sync_actual.get("sin_stock"):
                sinc.pop(nombre_real, None)
                sync_actual = {}

            # Actualizar precio solo si cambió desde último sync
            # Calcular precio objetivo SIN llamar a la API todavía
            p_obj = precio_objetivo(datos_prov["precio_base"])
            ultimo_sinc = sync_actual.get("precio", 0)

            # Solo sincronizar si el precio objetivo cambió desde el último sync
            if p_obj != ultimo_sinc:
                ok, p_min, p_max = sincronizar_producto(prod, datos_prov["precio_base"], datos_prov["variantes"])
                if ok:
                    sinc[nombre_real] = {"precio": p_min, "ts": time.time()}
                    rango   = f"${p_min:,}" if p_min == p_max else f"${p_min:,}–${p_max:,}"
                    etiqueta = f"({len(prod['variantes'])} var.)" if prod["tiene_variantes"] else "(sin var.)"
                    lineas_precios.append(
                        f"• *{nombre_real}* {etiqueta}\n"
                        f"  Antes: ${int(precio_web):,} → *Nuevo: {rango}*"
                    )

    # ── Enviar notificaciones ─────────────────────────────────────────────────
    if bloque_ofertas:
        tg(f"🏷️ *[{NOMBRE_TIENDA}] ¡Descuentos en el Proveedor!*\n\n{bloque_ofertas}")
    if bloque_nuevos:
        tg(f"🔥 *[{NOMBRE_TIENDA}] ¡Nuevo producto en el Proveedor!*\n\n{bloque_nuevos}")
    if bloque_recuperados:
        tg(f"🔄 *[{NOMBRE_TIENDA}] ¡Stock recuperado!*\n\n{bloque_recuperados}")
    for i in range(0, len(lineas_precios), 20):
        tg(f"💲 *[{NOMBRE_TIENDA}] Precios actualizados:*\n\n" + "\n\n".join(lineas_precios[i:i+20]))
    for i in range(0, len(lineas_sin_stock), 30):
        tg(f"📦 *[{NOMBRE_TIENDA}] Marcados sin stock:*\n\n" + "\n".join(lineas_sin_stock[i:i+30]))

    if not any([bloque_ofertas, bloque_nuevos, bloque_recuperados, lineas_precios, lineas_sin_stock]):
        print("✅ Sin cambios detectados.")

    db["productos_proveedor"] = prov_consolidado
    db["sincronizados"]       = sinc
    db["pedidos_procesados"]  = pedidos_proc
    db["api_token"]           = _token
    db["api_user_id"]         = _store_id
    escribir_db(db)
    print("─── ✅ Ciclo completado ───")

# ══════════════════════════════════════════════════════════════════════════════
# COMANDOS TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════
AYUDA = f"""
🤖 *Comandos disponibles:*

🔑 *Token / API*
`?code=XXXX` — Canjear código OAuth
`/estado_api` — Ver token activo
`/borrar_token` — Eliminar token

📦 *Productos (requiere token)*
`/listar` — Ver productos de tu tienda
`/ocultar NOMBRE` — Ocultar producto
`/publicar NOMBRE` — Publicar producto
`/stock NOMBRE CANTIDAD` — Cambiar stock
`/precio NOMBRE VALOR` — Cambiar precio

🔄 *Sincronización*
`/sync_total` — Actualizar TODOS los precios y stock
`/ciclo` — Forzar ciclo de monitoreo
`/exportar_precios` — Excel precios lista para importar

🔍 *Debug*
`/debug_match NOMBRE` — Ver qué encuentra el bot para un producto
`/debug_env` — Ver estado de variables de entorno

❓ `/ayuda` — Este mensaje
""".strip()

def procesar_cmd(texto):
    global _token, _store_id
    texto = texto.strip()

    # ── OAuth ──────────────────────────────────────────────────────────────
    if "?code=" in texto:
        code = texto.split("?code=")[1].split("&")[0].strip()
        tg("🔄 Canjeando código OAuth...")
        token = canjear_code(code)
        if token:
            tg(f"✅ *¡Token obtenido!*\n\n"
               f"Guardá en Railway → Variables:\n"
               f"• `API_TOKEN` = `{token}`\n"
               f"• `API_USER_ID` = `{_store_id}`")
        else:
            tg("❌ Token fallido. El código dura 1 minuto.")
        return

    cmd = texto.lower().split()
    if not cmd: return

    # ── /ayuda ──────────────────────────────────────────────────────────────
    if cmd[0] == "/ayuda":
        tg(AYUDA)

    # ── /estado_api ─────────────────────────────────────────────────────────
    elif cmd[0] == "/estado_api":
        if _token:
            tg(f"✅ *Token activo*\nStore ID: `{_store_id}`\nToken: `{_token[:12]}...`")
        else:
            tg("❌ Sin token. Mandá el `?code=` para obtenerlo.")

    # ── /borrar_token ────────────────────────────────────────────────────────
    elif cmd[0] == "/borrar_token":
        _token = _store_id = None
        db = leer_db()
        db.pop("api_token", None); db.pop("api_user_id", None)
        escribir_db(db)
        tg("🗑️ Token eliminado.")

    # ── /listar ──────────────────────────────────────────────────────────────
    elif cmd[0] == "/listar":
        if not _token: tg("❌ Necesito el token primero."); return
        tg("⏳ Cargando catálogo...")
        catalogo = obtener_catalogo(forzar=True)
        if not catalogo: tg("No encontré productos."); return
        lineas = []
        for p in catalogo[:50]:
            cv = len(p["variantes"])
            extra = f"({cv} var.)" if cv > 0 else "(sin var.)"
            icon = "✅" if p["published"] else "🚫"
            lineas.append(f"{icon} *{p['nombre']}* {extra} — ${int(p['precio_base']):,}")
        msg = f"📦 *{len(catalogo)} productos:*\n\n" + "\n".join(lineas)
        if len(catalogo) > 50: msg += f"\n\n_...y {len(catalogo)-50} más_"
        tg(msg)

    # ── /sync_total ──────────────────────────────────────────────────────────
    elif cmd[0] == "/sync_total":
        if not _token: tg("❌ Necesito el token primero."); return
        threading.Thread(target=run_sync_total, daemon=True).start()

    # ── /ocultar ─────────────────────────────────────────────────────────────
    elif cmd[0] == "/ocultar":
        if not _token: tg("❌ Necesito el token primero."); return
        nombre = " ".join(texto.split()[1:])
        if not nombre: tg("Uso: `/ocultar Nombre`"); return
        catalogo = obtener_catalogo()
        prod = next((p for p in catalogo if match(normalizar(nombre), p["nombre_norm"])), None)
        if prod:
            if set_visibilidad(prod["id"], False): tg(f"🚫 *{prod['nombre']}* ocultado.")
            else: tg("❌ No pude ocultar el producto.")
        else: tg(f"❌ No encontré *{nombre}*.")

    # ── /publicar ────────────────────────────────────────────────────────────
    elif cmd[0] == "/publicar":
        if not _token: tg("❌ Necesito el token primero."); return
        nombre = " ".join(texto.split()[1:])
        if not nombre: tg("Uso: `/publicar Nombre`"); return
        catalogo = obtener_catalogo()
        prod = next((p for p in catalogo if match(normalizar(nombre), p["nombre_norm"])), None)
        if prod:
            if set_visibilidad(prod["id"], True): tg(f"✅ *{prod['nombre']}* publicado.")
            else: tg("❌ No pude publicar el producto.")
        else: tg(f"❌ No encontré *{nombre}*.")

    # ── /stock ───────────────────────────────────────────────────────────────
    elif cmd[0] == "/stock":
        if not _token: tg("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3: tg("Uso: `/stock Nombre 10`"); return
        try: nuevo = int(partes[-1]); nombre = " ".join(partes[1:-1])
        except ValueError: tg("El último parámetro debe ser un número."); return
        catalogo = obtener_catalogo()
        prod = next((p for p in catalogo if match(normalizar(nombre), p["nombre_norm"])), None)
        if prod:
            if prod["variantes"]:
                ok = all(set_stock_variante(v["id"], nuevo) for v in prod["variantes"])
            else:
                ok = set_visibilidad(prod["id"], nuevo > 0)
            if ok: tg(f"📦 Stock de *{prod['nombre']}* → *{nuevo}*.")
            else:  tg("❌ No pude actualizar el stock.")
        else: tg(f"❌ No encontré *{nombre}*.")

    # ── /precio ──────────────────────────────────────────────────────────────
    elif cmd[0] == "/precio":
        if not _token: tg("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3: tg("Uso: `/precio Nombre 9999`"); return
        try: nuevo = int(partes[-1]); nombre = " ".join(partes[1:-1])
        except ValueError: tg("El último parámetro debe ser un número."); return
        catalogo = obtener_catalogo()
        prod = next((p for p in catalogo if match(normalizar(nombre), p["nombre_norm"])), None)
        if prod:
            if prod["variantes"]:
                ok = all(set_precio_variante(v["id"], nuevo) for v in prod["variantes"])
            else:
                ok = set_precio_producto(prod["id"], nuevo)
            if ok: tg(f"💲 *{prod['nombre']}* → *${nuevo:,}*.")
            else:  tg("❌ No pude actualizar el precio.")
        else: tg(f"❌ No encontré *{nombre}*.")

    # ── /exportar_precios ────────────────────────────────────────────────────
    elif cmd[0] == "/exportar_precios":
        tg("⏳ Generando Excel...")
        data, msg = generar_excel()
        if data:
            fecha = datetime.now().strftime("%d-%m-%Y")
            if not tg_archivo(data, f"precios_{fecha}.xlsx", caption=msg):
                tg("❌ Excel generado pero falló el envío.")
        else:
            tg(msg)

    # ── /ciclo ───────────────────────────────────────────────────────────────
    elif cmd[0] == "/ciclo":
        tg(f"🔄 *[{NOMBRE_TIENDA}]* Iniciando ciclo manual...")
        threading.Thread(target=ciclo_monitoreo, daemon=True).start()

    # ── /debug_match ─────────────────────────────────────────────────────────
    elif cmd[0] == "/debug_match":
        nombre = " ".join(texto.split()[1:]).strip()
        if not nombre: tg("Uso: /debug_match Nombre del producto"); return
        nombre_norm = normalizar(nombre)
        db   = leer_db()
        prov = db.get("productos_proveedor", {})
        if not prov: tg("Sin datos del proveedor."); return
        idx  = _construir_indice(prov)

        # Buscar en proveedor
        base_norm, datos_prov = _buscar_en_indice(nombre_norm, idx)
        lineas = [f"*Debug: {nombre}*", ""]
        if datos_prov:
            p_obj = precio_objetivo(datos_prov["precio_base"])
            lineas.append(f"PROVEEDOR: encontrado")
            lineas.append(f"  Base: {base_norm}")
            lineas.append(f"  Precio prov: ${datos_prov['precio_base']:,} → Web: ${p_obj:,}")
            if datos_prov["variantes"]:
                lineas.append(f"  Variantes prov: {len(datos_prov['variantes'])}")
                for vn, vp in list(datos_prov["variantes"].items())[:5]:
                    lineas.append(f"    - {vn}: ${vp:,}")
        else:
            lineas.append("PROVEEDOR: NO encontrado")
        lineas.append("")

        # Buscar en catálogo API
        catalogo = obtener_catalogo() if _token else []
        prod = next((p for p in catalogo if match(nombre_norm, p["nombre_norm"])), None)
        if prod:
            lineas.append(f"CATALOGO API: encontrado")
            lineas.append(f"  Nombre: {prod['nombre']}")
            lineas.append(f"  Variantes: {len(prod['variantes'])}")
            lineas.append(f"  Precio actual: ${int(prod['precio_base']):,}")
            if prod['variantes']:
                for v in prod['variantes'][:5]:
                    lineas.append(f"    - {v['nombre'] or '(sin nombre)'}: ${int(v['precio']):,}")
        else:
            lineas.append("CATALOGO API: NO encontrado" if _token else "CATALOGO API: sin token")

        tg("\n".join(lineas))

    # ── /debug_env ───────────────────────────────────────────────────────────
    elif cmd[0] == "/debug_env":
        t = os.environ.get("API_TOKEN","")
        u = os.environ.get("API_USER_ID","")
        lineas = [
            "TELEGRAM_TOKEN: " + ("OK" if TELEGRAM_TOKEN else "NO"),
            "CHAT_ID: "        + ("OK" if CHAT_ID else "NO"),
            "CLIENT_ID: "      + ("OK" if CLIENT_ID else "NO"),
            "SCRAPERAPI_KEY: " + ("OK" if SCRAPERAPI_KEY else "NO"),
            "API_TOKEN env: "  + (t[:10]+"..." if t else "NO ENCONTRADA"),
            "API_USER_ID env: "+ (u if u else "NO ENCONTRADA"),
            "Token memoria: "  + (str(_token)[:10]+"..." if _token else "NONE"),
            "UserID memoria: " + (_store_id or "NONE"),
            "NOMBRE_TIENDA: "  + (NOMBRE_TIENDA or "NO"),
        ]
        tg("Diagnóstico:\n" + "\n".join(lineas))

    else:
        tg("❓ Comando no reconocido. Mandá `/ayuda`.")

# ── Loop de escucha ───────────────────────────────────────────────────────────
def escuchar_telegram():
    if not TELEGRAM_TOKEN: return
    offset = 0
    url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    print("📡 Telegram activo...")
    while True:
        try:
            r = requests.get(f"{url}?offset={offset}&timeout=10", timeout=15)
            if r.status_code == 200:
                for u in r.json().get("result", []):
                    offset = u["update_id"] + 1
                    msg    = u.get("message", {})
                    texto  = msg.get("text", "")
                    cid    = str(msg.get("chat", {}).get("id", ""))
                    if cid != CHAT_ID or not texto: continue
                    print(f"📨 {texto[:60]}")
                    try: procesar_cmd(texto)
                    except Exception as e: print(f"❌ Cmd: {e}")
        except Exception as e:
            print(f"⚠️ Telegram loop: {e}")
        time.sleep(1)

# ══════════════════════════════════════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 Bot iniciado...")
    print(f"   API_TOKEN env:   {'SÍ (' + os.environ.get('API_TOKEN','')[:8] + '...)' if os.environ.get('API_TOKEN') else 'NO'}")
    print(f"   API_USER_ID env: {os.environ.get('API_USER_ID','NO')}")
    print(f"   NOMBRE_TIENDA:   {NOMBRE_TIENDA}")

    cargar_token()

    if _token:
        tg(f"🟢 *[{NOMBRE_TIENDA}] Bot iniciado* — Token activo (store_id: `{_store_id}`)\n\n"
           f"Mandá `/sync_total` para sincronizar precios y stock.\n"
           f"Mandá `/ayuda` para ver los comandos.")
    else:
        tg(f"🟡 *[{NOMBRE_TIENDA}] Bot iniciado* — Sin token API.\n"
           f"Mandá `/debug_env` para diagnosticar.")

    threading.Thread(target=escuchar_telegram, daemon=True).start()

    ciclo_monitoreo()
    while True:
        time.sleep(900)
        ciclo_monitoreo()
