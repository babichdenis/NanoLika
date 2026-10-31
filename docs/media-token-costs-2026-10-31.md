# Цена картинки у поставщиков с оплатой за токены

Замер 31.10.2026, ProxyAPI. Одна генерация на модель, промпт
«A small red cube on a white table», 1K, 1:1.

| Модель | Токенов (вход + выход) | Списано, ₽ | Ставка ProxyAPI, ₽/1M |
|---|---:|---:|---:|
| `black-forest-labs/flux-3-image-klein` | 4 184 (9 + 4 175) | **1,101** | 263,16 |
| `black-forest-labs/flux.2-klein-4b` | 4 105 (9 + 4 096) | **1,897** | 463,16 |
| `bytedance-seed/seedream-5-0-lite` | 16 393 (9 + 16 384) | **18,981** | 1 157,89 |
| `bytedance-seed/seedream-5-0-pro` | 16 393 (9 + 16 384) | **24,158** | 1 473,68 |

`google/gemini-3.1-flash-image` через ProxyAPI не поддерживается: `400 Model not
supported`.

Формула сходится до копейки:

```
цена = (вход + выход) × ставка ₽/1M / 1_000_000
4 184 × 263,16 / 1 000 000 = 1,101 ₽
```

## Что это значит

- Цена «за 1M токенов» у картинок не равна цене картинки: у FLUX Klein на
  картинку уходит ~4 тыс. токенов, у Seedream 5 — ~16 тыс. Поэтому в закупку
  медиа эти ставки не подставляем: получается 19–24 ₽ за картинку там, где
  Bothub/Polza продают ту же Seedream за 4–8 ₽.
- Дешёвые картинки у ProxyAPI — FLUX 3 Klein (~1,10 ₽) и FLUX.2 Klein 4B
  (~1,90 ₽). Но в нашем каталоге этих моделей нет; если добавлять, цену клиенту
  считать от 1,10–1,90 ₽, а не от 263 ₽/1M.
- Seedream 5 Lite/Pro у ProxyAPI дороже Bothub в 2–4 раза. Для закупки берём
  Bothub/Polza, а не токенную ставку.

## Где смотреть при следующем замере

- Баланс ProxyAPI: `GET https://api.proxyapi.ru/proxyapi/balance` (в админке
  «Поставщики» — строка ProxyAPI).
- Токены: поле `usage` в ответе
  `POST https://api.proxyapi.ru/v1/images/generations`
  (`input_tokens`, `output_tokens`, `total_tokens`).
- Прайс: `https://proxyapi.ru/pricing`, ставка за 1M токенов.
- Скрипт: `PROXYAPI_KEY=... python cloud/scripts/measure_image_cost.py --model ... --yes`
  (без `--yes` только показывает, что будет сгенерировано).

Для RouterAI баланс — `GET https://routerai.ru/api/v1/credits`, для Polza —
`GET https://polza.ai/api/v1/balance`; токены тоже в `usage` ответа.
