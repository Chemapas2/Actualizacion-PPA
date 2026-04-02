import streamlit as st
import fitz  # PyMuPDF
from PIL import Image
import io
import os

st.set_page_config(page_title="Monitor PPA - Actualización", layout="wide")

def extraer_pagina(doc, num_pag, zoom=2.5):
    if doc and len(doc) > num_pag:
        pagina = doc[num_pag]
        pix = pagina.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.open(io.BytesIO(pix.tobytes()))
    return None

# Cargar el documento
archivo = "Peste-Porcina-Africana-Situacion-Actual.pdf"
doc = fitz.open(archivo) if os.path.exists(archivo) else None

if not doc:
    st.error(f"No se encuentra el archivo: {archivo}")
else:
    st.title("🐖 Informe de Situación PPA")
    st.info("Actualización: 26 de marzo de 2026")

    # --- BLOQUE 1: DATOS CLAVE ---
    st.header("1️⃣ Resumen de Focos y Casos")
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Última Semana")
        # Basado en pág 2 y 7
        st.metric("Nuevos Focos", "1")
        st.metric("Nuevos Positivos (Jabalíes)", "6")
    
    with col2:
        st.subheader("Acumulado Total")
        # Basado en pág 3 y 7
        st.metric("Focos Totales", "41")
        st.metric("Jabalíes Positivos", "238")
        st.metric("Cerdo Doméstico", "0", delta_color="normal")

    st.divider()

    # --- BLOQUE 2: MAPAS Y MUNICIPIOS ---
    st.header("2️⃣ Localización y Municipios Afectados")
    c_mapa, c_lista = st.columns([2, 1])
    
    with c_mapa:
        img_mapa = extraer_pagina(doc, 3) # Página 4 (índice 3) tiene el mapa de municipios
        if img_mapa:
            st.image(img_mapa, caption="Mapa de Municipios en Zona Restringida")
            
    with c_lista:
        st.write("**Municipios con casos (10):**")
        municipios = ["Cerdanyola del Vallès", "Rubí", "Barcelona", "Sant Cugat del Vallès", 
                      "Sant Quirze del Vallès", "Terrassa", "Molins de Rei", 
                      "Sant Feliu de Llobregat", "Sant Just Desvern", "Sabadell"]
        for m in municipios:
            st.write(f"- {m}")

    st.divider()

    # --- BLOQUE 3: BIOSEGURIDAD Y CONTENCIÓN ---
    st.header("3️⃣ Medidas y Vallados")
    # Mostramos la página 6 (índice 5) que tiene el mapa de satélite y medidas
    img_bio = extraer_pagina(doc, 5)
    if img_bio:
        st.image(img_bio, caption="Aislamiento y Control Poblacional")
    
    st.info("**Medidas clave:** Refuerzo de vallados perimetrales, búsqueda de cadáveres y vigilancia pasiva en las 45 granjas de las zonas I y II.")

    st.divider()

    # --- BLOQUE 4: IMPACTO ECONÓMICO (MERCOLLEIDA) ---
    st.header("4️⃣ Evolución del Mercado y Precios")
    # La última página (pág 8, índice 7) tiene las gráficas y la tabla
    img_precios = extraer_pagina(doc, 7)
    if img_precios:
        st.image(img_precios, caption="Gráficas de Cotización: Cerdo Cebado y Lechón (Fuente: Mercolleida)")
    
    with st.expander("Ver detalle de cotización Mercolleida"):
        st.write("**Cotización 26 de Marzo:**")
        st.write("- Cerdo Selecto/Normal/Graso: +0,025 €/kg")
        st.write("- Lechón 20kg (Lleida): 65,00 € (0,00)")

    doc.close()