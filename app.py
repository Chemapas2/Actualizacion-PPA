import streamlit as st
import fitz  # PyMuPDF
from PIL import Image
import io
import os
import re

# FUERZA LA ACTUALIZACIÓN: Borra la memoria interna cada vez que se abre
st.cache_data.clear() 

st.set_page_config(page_title="Monitor PPA - Automatizado", layout="wide")

def extraer_datos_inteligente(doc):
    """Busca los números clave dentro del texto del PDF para automatizar la App"""
    texto_completo = ""
    for pagina in doc:
        texto_completo += pagina.get_text()
    
    # Buscamos patrones numéricos específicos en el texto
    def buscar_numero(patron, texto):
        match = re.search(patron, texto, re.IGNORECASE)
        return match.group(1) if match else "N/D"

    datos = {
        "focos_totales": buscar_numero(r"(\d+)\s+Focos totales", texto_completo),
        "casos_positivos": buscar_numero(r"(\d+)\s+Casos positivos", texto_completo),
        "animales_negativos": buscar_numero(r"([\d.]+)\s+Animales negativos", texto_completo),
        "fecha": buscar_numero(r"(\d+ de \w+ de 202\d)", texto_completo)
    }
    return datos

def extraer_pagina(doc, num_pag, zoom=2.5):
    if doc and len(doc) > num_pag:
        pagina = doc[num_pag]
        pix = pagina.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.open(io.BytesIO(pix.tobytes()))
    return None

archivo = "Peste-Porcina-Africana-Situacion-Actual.pdf"

if not os.path.exists(archivo):
    st.error(f"⚠️ No se encuentra '{archivo}' en GitHub.")
else:
    doc = fitz.open(archivo)
    datos = extraer_datos_inteligente(doc)
    
    st.title("🐖 Informe Automático de Situación PPA")
    st.info(f"📅 Datos extraídos del documento con fecha: {datos['fecha']}")

    # --- BLOQUE 1: DATOS DINÁMICOS ---
    st.header("1️⃣ Resumen Ejecutivo (Datos Reales)")
    c1, c2, c3 = st.columns(3)
    
    with c1:
        st.metric("Focos Totales", datos['focos_totales'])
    with c2:
        st.metric("Jabalíes Positivos", datos['casos_positivos'])
    with c3:
        st.metric("Animales Negativos", datos['animales_negativos'])

    st.divider()

    # --- BLOQUE 2: MAPAS ---
    st.header("2️⃣ Mapas de Situación")
    # Intentamos extraer la página 4 (donde suele estar el mapa principal)
    img_mapa = extraer_pagina(doc, 3) 
    if img_mapa:
        st.image(img_mapa, caption="Distribución Geográfica de los Focos", use_container_width=True)

    st.divider()

    # --- BLOQUE 3: MERCADO (LONJA) ---
    st.header("3️⃣ Evolución de Precios (Mercolleida)")
    # La última página siempre contiene la tabla de precios
    img_precios = extraer_pagina(doc, len(doc) - 1)
    if img_precios:
        st.image(img_precios, caption="Últimas Cotizaciones Oficiales", use_container_width=True)

    doc.close()
