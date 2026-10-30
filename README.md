# PPA España — Comité de Dirección | Nutreco / TechTeam

## Aplicación
Panel Streamlit de PPA en Cataluña: lectura de notas oficiales MAPA (PDF), indicadores epidemiológicos, mapas, medidas, evolución de las últimas notas y mercado porcino (Mercolleida / 3tres3).

### Archivo de arranque
`main.py`

### Dependencias
`requirements.txt`

### Logos
Incluir en la raíz del repositorio:
- `Logo Nutreco.jpg`
- `Logo TechTeam 2.jpg`
- `Solapa rosa trasparente.png` (o `Solapa rosa.jpg`)

## Ejecución
```bash
pip install -r requirements.txt
streamlit run main.py
```

En Streamlit Community Cloud, elegir el repositorio, rama y **main.py**.

## Frecuencia
Consulta automática cada **15 minutos mientras la página está abierta** y actualización manual con botón. La caché de las fuentes tiene TTL de 15 minutos; la actualización manual vacía esa caché.

## Cuando la lectura falla
Si la conexión o la extracción de PDF falla, el panel recupera la última lectura verificada guardada en `data_ppa/`, informa visiblemente del fallo y muestra la **fecha de la nota**, así como la **hora de la última lectura correcta**. Los valores que no pueden verificarse se muestran **N/D**, nunca con números inventados. Los PDFs recuperados quedan guardados para que puedan seguir viéndose y descargándose sin conexión.

### IMPORTANTE: conservación entre reinicios
El sistema de archivos de Streamlit Community Cloud puede ser **efímero**. La app ofrece **Descargar copia de lectura verificada (ZIP)** en la barra lateral, y **Restaurar datos verificados** desde un ZIP que hayas guardado. Mantener un archivo externo de respaldo (o almacenamiento persistente) es necesario si quieres conservar la copia cuando Streamlit reinicie o sustituya el contenedor.

Si el servidor nunca consiguió descargar ninguna nota, no dispone de cifras previas verificadas: aparecerá un aviso claro y los indicadores `N/D` hasta que la conexión se repare o se importe un ZIP de respaldo.

## Certificados HTTPS y seguridad
Nunca se usa `verify=False`. Si aparece `CERTIFICATE_VERIFY_FAILED`, hay que diagnosticar la cadena de certificados en el servidor donde corre Streamlit. Para una autoridad certificadora de confianza propia (por ejemplo, proxy de empresa), se puede instalar su **certificado CA raíz válido y autorizado** y configurar en el entorno:

```bash
REQUESTS_CA_BUNDLE=/ruta/al/ca-bundle.pem
```

Esto no debe ser un certificado arbitrario del servidor, ni se recomienda desactivar la comprobación TLS. Si la CA no está disponible en el entorno de despliegue, se conservará el último dato validado con advertencia de falta de actualización.

## Advertencias de interpretación
- La fecha de la última **nota** y la de la última **consulta correcta** no son lo mismo.
- Si un indicador procede de otra nota más antigua, se muestra su **fecha de origen**.
- La cotización de mercado puede quedar pendiente aunque la sección MAPA funcione (y viceversa).
- La aplicación no sustituye la revisión de los documentos oficiales enlazados.
