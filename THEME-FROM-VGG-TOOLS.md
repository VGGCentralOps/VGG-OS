# Theme from VGG Tools

Match `vggos.vgg.tools` to the parent Venture Garden Group app, not to the GreenHouse Capital or VigiPay tenant skins. Source of truth: `C:\Users\Admin\Downloads\VGG Tools\src\styles\index.css`, `src\components\ui\button.tsx`, `src\components\ui\input.tsx`, `src\components\ui\badge.tsx`, and `tailwind.config.ts`. The app forces light mode (`forcedTheme="light"` in `src\App.tsx`). Do not ship a dark page.

Copy the parent `:root` tokens below. Ignore `html[data-tenant='ghc']` and `html[data-tenant='vigipay']`. Those exist so subsidiary appraisal hosts look like their own logos. VGG OS is the group product, so it stays on the parent blue and green.

## Colour

Tokens are stored as HSL components and consumed as `hsl(var(--token))`. Hex values are the computed colour. The file’s own comments name the logo blue `#298BC4` and the logo green `#5DB644`. The HSL math lands one step beside those (`#298AC2` and `#5CB643`). Use the HSL tokens, not a new palette.

| Token | HSL | Hex | Use |
|---|---|---|---|
| `--background`, `--paper` | `195 78% 96%` | `#EDF9FD` | Page. A cool, almost-white blue. Not grey, not pure white. |
| `--foreground` | `204 38% 15%` | `#182935` | Body text. |
| `--card`, `--popover` | `0 0% 100%` | `#FFFFFF` | Cards, inputs, menus, dialogs. |
| `--card-foreground`, `--ink` | `204 32% 16%` | `#1C2B36` | Text on cards. |
| `--primary` | `202 65% 46%` | `#298AC2` | The one brand blue. Links, focus, selected state, primary filled actions that use the green-named variant. |
| `--primary-glow` | `202 72% 52%` | `#2C9CDD` | Hover or highlight only. Do not use it as a second brand. |
| `--primary-foreground` | `0 0% 100%` | `#FFFFFF` | Text on primary blue. |
| `--secondary` | `107 52% 91%` | `#E1F4DC` | Pale green wash behind a selected chip or a quiet panel. |
| `--accent` | `107 54% 86%` | `#D0EFC8` | Slightly stronger pale green. Still a wash, not a button fill. |
| `--paper-deep` | `154 48% 90%` | `#D9F2E7` | Hover fill for ghost and outline controls. |
| `--muted` | `196 52% 93%` | `#E4F1F6` | Code chips, empty wells, skeleton base. |
| `--muted-foreground` | `203 18% 37%` | `#4D626F` | Secondary text, captions, table headers, placeholders. |
| `--border`, `--input` | `191 38% 81%` | `#BCDAE1` | Hairline borders. Blue-grey, not neutral grey. |
| `--ring` | same as primary | `#298AC2` | Focus ring. |
| `--success` | `142 71% 35%` | `#1A9948` | Positive status. White text. |
| `--warning` | `38 92% 50%` | `#F59F0A` | Stale, pending, needs review. Dark ink text, not white. |
| `--destructive` | `0 72% 51%` | `#DC2828` | Failed sync, denied, missing-as-error. White text. |
| `--chart-1` | primary | `#298AC2` | First series. |
| `--chart-2` | `107 52% 49%` | `#58BE3C` | Second series. The saturated logo green. |
| `--chart-3` | `204 32% 22%` | `#263C4A` | Third series. Ink. |
| `--chart-4` | warning | `#F59F0A` | Fourth series. |
| `--chart-5` | destructive | `#DC2828` | Fifth series. |

Score colours exist for appraisals. Do not reuse them as finance traffic lights. A missing balance is muted text that says missing. It is not red and it is not zero.

`index.html` sets `<meta name="theme-color" content="#1a2e22">`. That dark green is browser-chrome leftover. The page people see is `#EDF9FD`. For VGG OS set `theme-color` to `#EDF9FD` or `#298AC2`.

Selection: background primary blue, text white.

Opacity recipes used on real surfaces:

- Card shadow: `0 2px 10px hsl(202 65% 46% / 0.08)`.
- Primary button glow: `0 2px 12px hsl(202 65% 46% / 0.28)`.
- Pressed button: `0 2px 8px hsl(202 65% 46% / 0.14)`.
- Hover lift shadow: `0 4px 14px hsl(202 65% 46% / 0.18)`.
- Portal card, only on the company picker: `0 8px 32px` at 12% accent, hover `0 14px 40px` at 18%. Do not use that heavier shadow on finance tables.
- Hero photo scrim: background at 22% opacity over the image.

There is no gradient fill on product screens. The only gradients are the sidebar scroll fades (solid sidebar colour fading to transparent) and the soft portal orbs. Finance screens stay flat: paper page, white card, hairline border, tinted shadow.

## Type

Load only these two families for the product UI:

```css
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap');
```

| Role | Family | Weights | Tracking |
|---|---|---|---|
| Body, buttons, labels, inputs, tables, nav | Manrope, then `system-ui, -apple-system, Segoe UI, Roboto, sans-serif` | 400 body, 600 labels and buttons, 500 captions | Body `normal`. Buttons `-0.004em`. |
| Headings, page titles, big figures | Plus Jakarta Sans, then Manrope, then the same system stack | 700 for h1–h3, 600 for h4 and figures | `-0.012em` on headings, `-0.01em` on display figures |

`tailwind.config.ts` still names DM Sans, Fraunces, and JetBrains Mono. A later block in `index.css` also loads Fraunces, Inter, and JetBrains Mono for the docs page. The utilities layer then forces `.font-display`, `.display-serif`, `.numeral`, and `.display-number` back to Plus Jakarta Sans with `!important`, and forces `.font-mono` back to Manrope. What people see in the app is Manrope and Plus Jakarta Sans. Do not install Fraunces, DM Sans, Inter, or JetBrains Mono for VGG OS.

Rendering: `text-rendering: optimizeLegibility`, antialiased on WebKit and Firefox.

Sizes:

| Element | Size | Line height | Weight |
|---|---|---|---|
| h1 | `1.45rem`, `1.875rem` from `sm`, `2.25rem` from `lg` | tight | 700 |
| h2 | `1.25rem`, `1.5rem` from `sm`, `1.875rem` from `lg` | tight | 700 |
| h3 | `1.05rem`, `1.25rem` from `sm`, `1.5rem` from `lg` | snug | 700 |
| h4 | `0.98rem`, `1.125rem` from `sm`, `1.25rem` from `lg` | snug | 600 |
| Body | `14px`, `15px` from `sm`. On viewports under `640px`, the body element itself is `15px`. | relaxed | 400 |
| Page titles inside the shell | Plus Jakarta Sans `text-lg` (`1.125rem`) to `text-2xl`, weight 600, tracking tight | tight | 600 |
| Eyebrow / section subtitle | `11px`, weight 500, muted colour | normal | 500 |
| Table body in long documents | `0.88rem` | normal | 400 |
| Table header | `0.875rem`, weight 500, muted colour | normal | 500 |
| Badge | `10px`, weight 500, uppercase, tracking `0.18em` | normal | 500 |
| Button | `12px` (`text-xs`), weight 500 in the component and 600 from the global `button` rule. The global rule wins on a raw button. | normal | 600 |

Figures that are amounts use Plus Jakarta Sans, weight 600, `font-variant-numeric: tabular-nums`, line-height 1. Keep currency, scale, and “missing” in Manrope at `14px` or `11px` muted, beside the figure, never inside the same glyphs. A dash for an empty value is the caption “missing” or an em dash in muted text. Do not colour it red.

Login and portal wordmarks use Plus Jakarta Sans at `1.65rem` to `1.75rem`, weight 600, tracking `-0.02em`.

## Icons

Library: `lucide-react` `^0.462.0`. One set only. Do not mix Heroicons, emoji, or filled pictograms.

Defaults: stroke width 2 (Lucide’s own default), `currentColor`, `aria-hidden` unless the icon is the only label. Sizes that already appear:

| Place | Classes | Pixels |
|---|---|---|
| Inside a button | `size-3.5`, pointer-events none, no shrink | 14 |
| Inline with a label | `h-4 w-4` | 16 |
| Sidebar glyph | `h-4 w-4` or `h-[18px] w-[18px]` | 16–18 |
| Collapse control | the Lucide icon at 16px inside a 36px hit area | 16 |

Colour is the text colour of the control. Idle icons are `foreground` or `muted-foreground`. A primary action’s icon is white because it sits on blue. Status icons use success, warning, or destructive, and they always sit next to a word. Do not communicate stale, failed, or missing by colour alone.

The appraisal sidebar paints each destination a different tile (fuchsia, sky, violet, emerald, amber, indigo) with its own corner radius. That rainbow is navigation confetti for the people app. Do not copy it into finance. VGG OS nav icons stay one colour: muted ink when idle, primary blue when the section is current, on a pale green or white square with a single radius of `8px`.

Icons worth using, all Lucide:

| Meaning | Icon |
|---|---|
| Group / companies | `Building2` |
| Ledger, trial balance | `Scale` or `BookOpen` |
| Bank account | `Landmark` |
| Sync running | `Loader2` with `animate-spin` |
| Sync failed | `AlertCircle` |
| Synced | `CheckCircle2` |
| Stale or waiting | `Clock` |
| Missing feed | `Unplug` |
| Export | `Download` |
| Filter | `ListFilter` |
| Close | `X` |
| Back | `ArrowLeft` |
| Sign out | `LogOut` |
| Show or hide a number | `Eye` and `EyeOff` |
| Locked period | `Lock` |

Spinner is `Loader2` only, `h-4 w-4 animate-spin`, colour muted or primary. Do not invent a custom loader.

## Shape, elevation, space

| Token | Value |
|---|---|
| `--radius` | `0.5rem` (8px) |
| Cards, panels, tags, score chips | `rounded-md` (8px) |
| Buttons, inputs, badges | `rounded-sm` (6px) |
| Icon button | `h-10 w-10` (40px) |
| Default button | height 44px, horizontal padding 20px |
| Small button | height 36px, padding 14px, `10.5px` type |
| Large button | height 48px, padding 24px |
| Input | height 44px, padding `14px` by `10px`, text `14px`, radius 6px |
| Card padding | `20px`, `28px` from `sm` on a section shell |
| Sidebar width | `18rem` (288px) |
| Content max | Tailwind container centred, padding `2rem`, cap `1400px` |
| Page stack gap | about `1rem` to `1.5rem` between sections |

Borders are 1px `--border`. Focus is a 1px primary ring with 2px offset, plus the border turning foreground on inputs. Disabled controls are `opacity-50` and not clickable.

Cards do not use a heavy drop shadow. The product shadow is the 2px/10px primary tint above. Portal marketing cards on the company picker are the exception (`rounded-2xl`, blur, larger shadow). Finance reports use the product card, not the portal card.

## Controls

Buttons, from `button.tsx`. Labels are short. The component asks for uppercase and wide tracking because it also sets `font-mono`. The live font override turns that into Manrope, so uppercase wide-tracked Manrope is what the component will render if you copy the classes. For VGG OS, prefer the legacy classes in `index.css`, which are the calmer ones:

- Filled ink: background `--foreground`, text `--background`, height 40px, radius 8px, type 14px weight 500. Use for the main action on a page.
- Filled blue: background `--primary`, white text, the 12px primary glow. Use for the one action that commits (publish, export, start sync).
- Outline: white card, 1px border at foreground 20%, hover border foreground and a `--paper-deep` wash.
- Ghost: no border, hover `--paper-deep` at 60%.
- Destructive: `--destructive` with white text. Rare. Confirm before a destructive action.
- Link: primary blue, underline on hover, normal case, no tracking.

Do not put two filled blue buttons in the same cluster.

Badges are 10px uppercase, radius 6px, padding `8px` by `2px`. Default badge is ink on paper-inverse (foreground fill, background text). Green badge is primary blue fill, despite the variant name `green`. Outline badge is white with a hairline and muted text. Warning badge is amber with ink text.

Inputs: white, 1px border, 6px radius, placeholder in muted foreground. Focus border becomes foreground and a 1px ring of primary at 20% opacity. No inner shadow.

Tags and chips: 11px, weight 500, radius 8px, padding `8px` by `4px`, hairline border, white or primary fill. Gap inside the chip is `6px` when it holds an icon.

Tables, taken from the docs prose which is the closest long-form table:

- Full width, collapse borders, body `0.88rem`.
- Cell padding `0.55rem 0.75rem`.
- Row divider is 1px `--border`.
- Header is muted, weight 500, with a slightly stronger bottom border `1.5px` at foreground 15%.
- Numeric columns align right and use tabular numbers.
- Do not zebra-stripe with a new grey. If a row needs separation, use `--muted` at a low opacity or nothing.

## Motion

One entrance: opacity 0 and `translateY(12px)` to rest, `0.6s` with `cubic-bezier(0.16, 1, 0.3, 1)`. Use it once on a page entering, not on every card.

Hover on a marketing card: `300ms`, lift `-translate-y-0.5` (2px). Product rows do not lift.

Accordion: `0.2s ease-out`.

Skeleton: opacity pulse. Fast is `0.65s` between 1 and 0.52. Slow is `1.35s` between 1 and 0.38. Skeleton colour is `--muted`. Never pulse a number that could be read as a live balance.

Scrollbars: hidden under `1440px` width (`scrollbar-width: none`). A visible thin bar is 6px, thumb foreground at 25% opacity, square (radius 0). Sidebar nav keeps an 8px rounded thumb at 35% of the current text colour.

Tap highlight on phones is transparent.

## Logo and images

Parent mark: `C:\Users\Admin\Downloads\VGG Tools\public\vgg-logo.webp`, and the same asset under `public\email\vgg-logo.webp`. Favicon and apple touch icon: `public\favicon.png`. On a light header the logo sits at height 28px, or 32px from `sm`, with its natural width. Do not recolour it. Do not place it on a dark bar. The parent sidebar is the same light paper as the page, so the logo sits on `#EDF9FD` or white.

There is no VGG OS mark yet. Until one exists, use the parent logo plus the words “VGG OS” in Plus Jakarta Sans, 600, tracking `-0.02em`, colour `--foreground`.

Photography in the appraisal app (hero jpgs under `src\assets`) is for login and the company picker. Do not use those photos on a trial balance or a bank register.

## What a finance screen should look like

Page background `#EDF9FD`. A white card with a 1px `#BCDAE1` border, 8px radius, 20px padding, and the soft blue shadow. Title in Plus Jakarta Sans. Body and table in Manrope. One blue button. Status as a word plus a Lucide icon. Amounts in tabular Plus Jakarta Sans. Muted captions for the source, the as-of time, and the currency. Plenty of the paper colour showing around the card. No dark sidebar, no rainbow tiles, no serif display face, no second green used as a fill.
