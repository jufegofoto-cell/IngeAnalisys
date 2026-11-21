# Actualizar IngeAnalisys a la versión 1.11.0

Todo desde el navegador, con la cuenta **jufegofoto-cell** (la que figura en
`author_url` de la ficha). Si no publicaste la 1.10.0, sáltala: esta la
incluye, pero haz también su paso 1 (abajo, «Si vienes de la 1.9 o antes»).

## 1. Repositorio (github.com/jufegofoto-cell/IngeAnalisys)

1. **Add file ▸ Upload files**.
2. Arrastra **todo el contenido** de la carpeta `repositorio/`, incluida la
   carpeta `fuentes/` (arrástrala como carpeta: GitHub la sube con sus 9
   archivos). Reemplazan a los actuales.
3. **Commit changes** («Versión 1.11.0»).
4. Comprueba que en el repositorio aparecen `fuentes.py` y la carpeta
   `fuentes/` con 8 archivos de fuente y `OFL.txt`.

Si vienes de la 1.9 o antes: borra también `osm.py` y `geometria.py` (abre cada
uno, menú **⋯ ▸ Delete file** y **Commit changes**).

## 2. Versión (release)

**Releases ▸ Draft a new release**:
- *Choose a tag*: escribe `V1.11.0` (V mayúscula) ▸ «Create new tag».
- *Release title*: `V1.11.0`.
- Adjunta `release/analisis_sitio.zip` **tal cual** (no lo descomprimas ni lo
  vuelvas a comprimir). Su huella, ya puesta en la ficha, es
  `2a87d80a37b025107d83973fb211ae33b6c7388adf20e7057921d1eb6b3292b9`.
- **Publish release**.

## 3. Ficha del catálogo (github.com/ingelibre/ingetrazo-extensions)

1. Abre `extensions/analisis_sitio.toml` y pulsa el lápiz (**Edit this file**).
2. Borra todo y pega el contenido de `catalogo/analisis_sitio.toml`. Cambian
   `version`, `download` y `sha256` (y el resumen, si vienes de la 1.8.0).
3. **Commit changes… ▸ Propose changes ▸ Create pull request.** No toques
   ningún otro archivo en esa solicitud.

## Qué pasará después

La revisión automática no encuentra nada que leer (el módulo no usa la red ni
ejecuta programas; las fuentes son archivos de datos). Si la envías desde
jufegofoto-cell y la huella coincide, **se fusiona sola** en unos minutos.
Si el robot dice que la `sha256` no coincide, su mensaje trae el valor
correcto: edita la ficha en la solicitud y ponlo.

## Cambios en la 1.11.0

- Textos del diagrama con fuentes libres sin serifa (licencia SIL OFL 1.1),
  incluidas en el módulo, sin instalar nada: **Atkinson Hyperlegible**
  (predeterminada), Noto Sans, Inter y Source Sans 3. La lista muestra cada
  una en su propia fuente. Las leyendas y rótulos de la vista también usan
  Atkinson Hyperlegible.
- Los documentos guardados con una fuente que ya no se ofrece (Arial,
  Georgia…) pasan a Atkinson Hyperlegible al abrirlos.
- El ZIP pasa de ~65 kB a ~1,5 MB por las fuentes (el límite del catálogo
  es 5 MB).

## Cambios en la 1.10.0 (por si no la publicaste)

- Panel en 7 secciones: 1 Ubicación, 2 Diagrama solar, 3 Rosa de los vientos,
  4 Datos climáticos, 5 Visualización, 6 Radiación solar y sombras, 7 Exportar
  imagen. Fuera «Mapa base y contexto» (OpenStreetMap) y «Masas».
- Cuadro «Textos del diagrama»: fuente, tamaño y negrita.
