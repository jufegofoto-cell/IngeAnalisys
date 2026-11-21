# Análisis de Sitio — extensión para IngeTrazo

**Sol · Viento · Clima · Radiación · Sombras**, sobre el propio modelo de IngeTrazo.
Es la app web *Análisis de Sitio 3D* convertida en extensión, como un plugin de
SketchUp: no modifica el núcleo; todo entra por `setup(app)` (API de extensiones 2).

## Instalación

1. En IngeTrazo: **Extensiones ▸ Abrir carpeta de extensiones**
   (Windows `%APPDATA%\ingetrazo\plugins\`, Linux/macOS `~/.local/share/ingetrazo/plugins/`).
2. Copia ahí **sólo** la carpeta `analisis_sitio` del ZIP (la carpeta `tests` no hace falta).
   Debe quedar `plugins/analisis_sitio/__init__.py`.
3. Reinicia IngeTrazo. Aparece la pestaña **Análisis de sitio** en la bandeja lateral
   y el submenú **Extensiones ▸ Análisis de sitio**.

Requisitos: IngeTrazo ≥ 0.6 (API 2). Sin dependencias extra (PySide6 + NumPy, ya incluidos).

## La pestaña, en orden

Las mismas siete secciones, en el mismo orden y con los mismos nombres, que la
extensión «Análisis de sitio» para SketchUp. El mapa base, el contexto y los volúmenes
del proyecto se trabajan con las herramientas de IngeTrazo.

1. **Ubicación** — cuatro bloques:
   - *Clima (archivo EPW)*: abrir o quitar el EPW y los datos de su estación.
     Con un EPW cargado, la ubicación y la zona horaria del sitio son las de su estación.
   - *Sitio*: búsqueda o «lat, lon», elegir en el mapa, latitud, longitud y zona horaria.
     Se escriben a mano cuando no hay EPW (con EPW quedan bloqueadas; al quitarlo vuelven a
     estar disponibles). Es el datum del proyecto en IngeTrazo.
   - *Norte del proyecto*: perilla, ángulo, ±15°, o «desde la arista seleccionada». Es el
     norte del proyecto de IngeTrazo (el del panel de georreferencia y el de sus sombras):
     cambiarlo aquí o allá es lo mismo. Gira sol, diagrama, mapa y terreno; la geometría
     no se mueve.
   - *Periodo de análisis*: meses y horas del EPW que cuentan para las estadísticas, la
     rosa de los vientos, los vientos dominantes y la radiación del «Periodo de análisis».
2. **Diagrama solar** — en la cabecera, los controles del diagrama: *Mostrar diagrama solar*
   (apagado por defecto), radio, centro, meses, banda, analemas, etiquetas, *Trayectoria del
   día*, *Rayo solar al centro* y colores. Activar *Mostrar diagrama solar* enciende la banda,
   los analemas, las etiquetas, la trayectoria del día y el rayo; sin él tampoco se dibujan
   la brújula (puntos cardinales y grados) ni los anillos. Al desmarcar *Trayectoria del día*
   se ocultan también el sol del momento y su rayo, y el rayo queda desmarcado. Al iniciar,
   el sol y su rayo están ocultos (trayectoria del día y rayo desmarcados). Colores: *Diagrama*
   (líneas, banda, puntos, etiquetas y brújula) y *Sol* (sol, rayo y trayectoria del día).
   Cuadro **Textos del diagrama**: fuente, tamaño (10 = normal) y negrita de las horas, los
   meses, los grados, los puntos cardinales y los anillos. Fuentes libres sin serifa
   incluidas en el módulo (no hace falta instalarlas): **Atkinson Hyperlegible**
   (predeterminada), Noto Sans, Inter y Source Sans 3. Debajo, fecha, hora y
   sombras: mueve las sombras reales de IngeTrazo; sol, amanecer/ocaso y clima a esta hora.
3. **Rosa de los vientos** — gráfico y color de la rosa, rosa 3D sobre el suelo (apagada por
   defecto), flecha del viento horario (azul), flechas animadas (apagadas por defecto),
   tabla de direcciones y «Aplicar vientos dominantes».
4. **Datos climáticos** — temperatura y humedad relativa mensual del EPW; guardar los
   gráficos en PNG.
5. **Visualización** — mostrar/ocultar el análisis, brújula y anillos (visibles con el
   diagrama solar), escala de las etiquetas de vientos y paneles, y convenciones (leyenda
   de lo visible).
6. **Radiación solar y sombras** — ambos se calculan sobre el modelo y se ven
   en la vista principal, como mapa de color sobre las superficies con su
   **escala gráfica** en un panel flotante arriba a la derecha (debajo del de
   información si ambos están visibles), con su casilla para mostrarlo u ocultarlo.
   Un mapa a la vez; se cambia en «Resultado en la vista».
   - *Superficies*: todo el modelo, la selección o sólo el suelo; con «Suelo alrededor»
     se añade una malla de suelo (margen automático o fijo), recortada bajo las caras
     que ya están a ras de suelo. Resolución por cara o malla de 2 / 1 / 0.5 m.
   - *Radiación solar incidente*: día de la sección 2, un mes, el periodo de análisis o
     el año. Directa con sombras por trazado de rayos contra todo lo visible del modelo
     + difusa isótropa con la vista real al cielo (Tregenza) + reflejada
     por el suelo (albedo). EPW, o cielo despejado teórico sin EPW. Paletas Ecotect,
     grises o térmica; escala automática o fija. kWh/m².
   - *Estudio de sombras*: el día de la sección 2, de una hora a otra, cada 15, 20, 30 o
     60 minutos; cada punto acumula las horas en sombra (sombra arrojada y propia).
     Se muestra como **horas de sombra** u **horas de sol (asoleamiento)**, en grises
     (blanco = sol, negro = sombra) o en **colores por horas estilo Ecotect** (una clase
     por hora, de amarillo = sol a azul marino = sombra, con su leyenda por clases).
     Al pasar de sombra a sol cada punto conserva su color; cambian los rótulos.
     En sombras, las paletas Ecotect y Térmica se leen al revés que en radiación:
     amarillo / blanco = lo más iluminado, azul / negro = lo más sombreado.
   - Los mapas son grupos bloqueados en la capa «Radiación solar»: se guardan en el
     .igz, se quitan y se deshacen en un paso. No se generan informes PDF.
7. **Exportar imagen** — PNG de la vista (hasta 5K), con el mapa y su escala si están
   visibles. Al final, la casilla **Mostrar panel flotante de información y
   convenciones** (título y posición configurables). Está oculto al iniciar
   IngeTrazo; si se muestra, también sale en la imagen.

La rueda del ratón desplaza la pestaña: los desplegables y casillas numéricas sólo
cambian con la rueda después de hacer clic en ellos.

Todo cambio de contenido o de mapas es **un paso de deshacer**.

## Diferencias con la app web

- La importación de OBJ/DXF, el modelado (volúmenes), el mapa base y el contexto los
  hace IngeTrazo (desde la versión 1.10.0 la extensión no los duplica ni usa la red).
- Sin anotaciones (desde la versión 1.9.0) ni botones de vistas de cámara: las vistas son
  las de IngeTrazo.

## Datos en el documento

`plugin_data["analisis_sitio"]` (vientos — con deshacer),
`analisis_sitio.vista` (ajustes de visualización), `analisis_sitio.clima` (EPW compacto).
Los mapas de radiación y sombras: grupos de la capa «Radiación solar».

## Estructura

```
analisis_sitio/
  __init__.py     setup(app): panel, overlay, selección, menús
  nucleo.py       controlador: sitio, hora (= sombras), clima, animación
  datos.py        valores en el documento y el comando de deshacer
  clima.py        EPW, estadísticas, rosa, paletas OKLab
  solar.py        trayectorias, analemas, banda, salida/puesta
  acciones.py     operaciones sin interfaz (las usan panel, menús y pruebas)
  dibujo.py       todo lo que se pinta sobre la vista
  graficos.py     rosa de vientos y clima mensual
  panel.py        la pestaña lateral
  herramientas.py herramienta de un clic con inferencias
  exportar.py     imagen PNG y gráficos del clima
  radiacion.py    radiación incidente y horas de sombra (trazado de rayos)
```

Pruebas: `tests/test_analisis_sitio.py` (copiar a `tests/` del repositorio y poner la
carpeta `analisis_sitio` en la raíz o en `plugins/`), `python -m pytest tests/test_analisis_sitio.py`.

Licencia: GPL‑3.0‑or‑later, como IngeTrazo. Las fuentes de la carpeta `fuentes/` se
distribuyen sin modificar bajo la SIL Open Font License 1.1 (`fuentes/OFL.txt`).
