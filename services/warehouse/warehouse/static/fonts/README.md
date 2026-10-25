# Fonts

Free stand-ins for the brand fonts until the licensed files are supplied:

| File | Stands in for | Source |
|---|---|---|
| `libre-caslon-text-700.woff2` | Cochin (headlines) | Libre Caslon Text 700, latin subset, from `@fontsource/libre-caslon-text@5.3.0` |
| `lexend-400.woff2`, `lexend-600.woff2` | BC Novatica (body) | Lexend 400 and 600, latin subset, from `@fontsource/lexend@5.3.0` |

Both are under the SIL Open Font License 1.1 (`OFL-*.txt`). Served by the Warehouse Service itself, so the page doesn't depend on a font host over a weak warehouse connection. To use the real brand fonts, put their `.woff2` files here and change the `@font-face` rules at the top of `../index.html`.
