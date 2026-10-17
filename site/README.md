# NanoLika site draft

`index.html` is the Russian landing page for the NanoLika product concept. It is a
static page with a custom illustration built from HTML and CSS, local Figtree fonts,
and a small script for the mobile navigation. It has no account, payment, or admin
flow yet.

## Preview

The local preview at `http://127.0.0.1:8099/` serves this directory. To start a
separate preview from the repository root:

```bash
cd site && python3 -m http.server 8000
```

Then open <http://127.0.0.1:8000/>.

## Files

| File | Purpose |
|---|---|
| `index.html` | NanoLika landing page, content, and device illustrations. |
| `nanolika.css` | Brand palette, responsive layout, illustration, and motion preferences. |
| `nanolika.js` | Mobile navigation and current-year footer. |
| `nanolika-icon.svg` | NanoLika favicon. |
| `legacy.html`, `style.css`, `site.js` | The earlier nanoMuse presentation; retained separately. |
| `promo/` and `media/` | The earlier nanoMuse promo storyboard and rendered film. |

The domain, signup, payment, and admin experiences are not connected in this draft.
