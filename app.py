import streamlit as st
import fitz  # PyMuPDF
from PIL import Image
import io
import os

# FUERZA LA ACTUALIZACIÓN: Borra la memoria interna para leer el PDF nuevo de GitHub
st.cache_data.clear() 

st.set_page_config(page_title="Monitor PPA - Actualización", layout="wide")

def extraer_pagina(doc, num_pag, zoom=2.5):
    if doc and len(doc) > num_pag:
        pagina = doc[num_pag]
        pix = pagina.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.open(io.BytesIO(pix.tobytes()))
    return None

# Nombre del archivo único
archivo = "Peste-Porcina-Africana-Situacion-Actual.pdf"

if not os.path.exists(archivo):
    st.error(f"⚠️ No se encuentra el archivo: {archivo}. Asegúrate de que el nombre en GitHub sea exacto.")
else:
    doc = fitz.open(archivo)
    
    st.title("🐖 Informe de Situación PPA")
    st.info("Última actualización detectada en el documento: 26 de marzo de 2026")

    # --- BLOQUE 1: DATOS CLAVE (Extraídos del Resumen Ejecutivo) ---
    st.header("1️⃣ Resumen de Focos y Casos")
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Última Semana")
        # Datos según pág 2 y 7 del PDF
        st.metric("Nuevos Focos", "1")
        st.metric("Nuevos Positivos (Jabalíes)", "6")
    
    with col2:
        st.subheader("Acumulado Total")
        # Datos según pág 3 y 7 del PDF
        st.metric("Focos Totales", "41")
        st.metric("Jabalíes Positivos", "238")
        st.metric("Cerdo Doméstico", "0", delta_color="normal")

    st.divider()

    # --- BLOQUE 2: MAPAS Y MUNICIPIOS ---
    st.header("2️⃣ Localización y Municipios Afectados")
    c_mapa, c_lista = st.columns([2, 1])
    
    with c_mapa:
        # Página 4 contiene el mapa de municipios
        img_mapa = extraer_pagina(doc, 3) 
        if img_mapa:
            st.image(img_mapa, caption="Mapa de Municipios en Zona Restringida")
            
    with c_lista:
        st.write("**Municipios afectados (10):**")
        # Lista extraída de la página 4 y 7
        municipios = [
            "Barcelona", "Cerdanyola del Vallès", "Molins de Rei", "Rubí", 
            "Sabadell", "Sant Cugat del Vallès", "Sant Feliu de Llobregat", 
            "Sant Just Desvern", "Sant Quirze del Vallès", "Terrassa"
        ]
        for m in municipios:
            st.write(f"- {m}")

    st.divider()

    # --- BLOQUE 3: BIOSEGURIDAD Y CONTENCIÓN ---
    st.header("3️⃣ Medidas y Vallados")
    # Página 6 contiene el mapa satelital de vallados y medidas
    img_bio = extraer_pagina(doc, 5)
    if img_bio:
        st.image(img_bio, caption="Zonas de vallado y control de agentes rurales")
    
    st.warning("**Aviso:** La situación se mantiene contenida en fauna silvestre. Las 45 granjas de la zona permanecen libres de infección.")

    st.divider()

    # --- BLOQUE 4: IMPACTO ECONÓMICO (MERCOLLEIDA) ---
    st.header("4️⃣ Evolución del Mercado y Precios")
    # Página 8 contiene las gráficas y la tabla de Mercolleida
    img_precios = extraer_pagina(doc, 7)
    if img_precios:
        st.image(img_precios, caption="Gráficas de Cotización y Evolución (Fuente: Mercolleida)")
    
    with st.expander("Ver detalle de cotización Mercolleida (26/03/2026)"):
        st.write("- **Cerdo Cebado:** +0,025 €/kg (Subida moderada)")
        st.write("- **Lechón 20kg:** 65,00 € (Sin cambios)")
        st.write("- **Cerdo Selecto:** 1,282 €/kg")

    doc.close()
