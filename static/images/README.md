# Static images for the public one-pager

Drop your project renders / photos in this folder with these exact filenames.
The one-pager will pick them up automatically. Until they exist, the page
falls back to nice gradient placeholders so the layout still looks good.

## Expected files

| Filename | Used in | Recommended size | Aspect |
|---|---|---|---|
| `hero.jpg` | Top hero (full-bleed background) | 1920 × 1080 or larger | 16:9 landscape |
| `aerial.jpg` | "Over de plek" section, right column | 1200 × 900 | 4:3 landscape |
| `straat.jpg` | "Wie wonen er straks" section | 1200 × 900 | 4:3 landscape |
| `doorsnede.jpg` | "Bewoner?" CTA section background | 1600 × 900 | 16:9 landscape |

Tips:
- JPG quality 85 is sweet spot for these. WebP also accepted if you rename
  the file extension in `templates/index.html`.
- Keep the most important visual content roughly centered. The hero and
  CTA images get darkened with an overlay so text on top stays legible.
- The page will auto-cache via Flask's static handler. After uploading
  new images, do a hard refresh (cmd+shift+R) to bypass browser cache.
