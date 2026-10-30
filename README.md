# PPA España — Comité de Dirección (Nutreco / TechTeam)

**Versión 3.2 — 30 de octubre de 2026**

Panel Streamlit que analiza notas PDF del MAPA sobre la peste porcina africana en Cataluña y muestra cifras epidemiológicas, mapas, medidas oficiales, evolución y referencias del mercado porcino.

## Archivos para GitHub

Coloca en la **raíz de tu repositorio**:

- `main.py`
- `requirements.txt`
- `README.md`
- `Logo Nutreco.jpg`
- `Logo TechTeam 2.jpg`
- `Solapa rosa trasparente.png` (y opcionalmente `Solapa rosa.jpg`)
- `Peste-Porcina-Africana-Situacion-Actual.pdf` *(opcional: ya aparece en tu repositorio; puede servir de respaldo si es una nota MAPA válida)*

El archivo de entrada de Streamlit Community Cloud es `main.py`. Comprueba que la **rama** y el **repositorio** configurados en Streamlit coinciden con los de GitHub.

## Despliegue

1. Añade o sustituye los archivos anteriores en GitHub.
2. En Streamlit Community Cloud, abre `Manage app` → `Settings` y comprueba repositorio, rama y `Main file path = main.py`.
3. Desde `Manage app`, elige `Reboot app` si el cambio no se aplica.
4. Debajo del encabezado debe aparecer **VERSIÓN 3.2 · 30/10/2026 · inicio seguro y recuperación local**.

Para ejecutar localmente:

```bash
pip install -r requirements.txt
streamlit run main.py
```

## Recuperación de datos y errores SSL

Si falla la conexión SSL al MAPA, la app no desactiva la verificación TLS. En su lugar:

1. Recupera su última lectura verificada guardada, si existe.
2. En un despliegue nuevo sin lectura anterior, intenta usar el PDF `Peste-Porcina-Africana-Situacion-Actual.pdf` que ya está en el repositorio. **Solo acepta el PDF si reconoce fecha, PPA, Cataluña y datos epidemiológicos.**
3. Si tampoco puede validar ese documento, muestra un aviso y `N/D`, sin inventar cifras. Se puede cargar manualmente la última nota oficial desde la barra lateral.
4. Indica la **fecha del documento** y advierte si se están mostrando datos conservados, que podrían estar desactualizados.

La lectura se actualiza cada 15 minutos mientras la aplicación está abierta. Streamlit Community Cloud puede reiniciar el almacenamiento temporal: conviene descargar la copia de seguridad ZIP que ofrece la aplicación y guardarla fuera del servidor.

## Correcciones incorporadas en v3.2

- Evita una excepción identificada cuando la app arranca sin datos y una URL PDF vale `None`.
- Evita caídas por ausencia de los logos: en ese caso presenta cabeceras de texto.
- Añade recuperación desde el PDF preexistente en el repositorio si el MAPA no puede leerse.
- Disminuye el uso de memoria al no duplicar en la caché grandes PDFs ni imágenes renderizadas.
- Mantiene los avisos de error, los datos verificados y la fecha de origen, sin desactivar SSL.

## Si aparece la pantalla «Oh no»

La documentación oficial de Streamlit indica que también puede deberse a un **límite de recursos de Community Cloud**, un fallo en las dependencias o una excepción de Python. No es posible diagnosticarlo a partir de esa pantalla por sí sola.

Abre la app estando identificado → `Manage app` (esquina inferior derecha) → `Cloud logs` y consulta las últimas líneas del error. Si no existe un traceback, revisa los límites de memoria y prueba un reinicio.

Referencia: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app

## Límites

Este código se ha comprobado sintácticamente y se ha ejecutado con una simulación de error de conexión y sin datos previos, pero **no se ha desplegado ni se ha probado con el certificado real del MAPA desde Streamlit Community Cloud**. La aplicación no garantiza que una fuente externa siempre esté disponible.
