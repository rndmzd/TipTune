---
name: TipTune
description: Incumbent dashboard language and the bounded browser-overlay extension
colors:
  primary: "#5b8cff"
  primary-weak: "rgba(91, 140, 255, 0.16)"
  bg: "#0b0c10"
  surface: "#111318"
  surface-2: "#0e1014"
  border: "rgba(255, 255, 255, 0.08)"
  border-strong: "rgba(255, 255, 255, 0.12)"
  text: "rgba(255, 255, 255, 0.92)"
  muted: "rgba(255, 255, 255, 0.62)"
  broadcast-text: "#f2f3f5"
typography:
  headline:
    fontSize: "20px"
    fontWeight: 650
    letterSpacing: "0.2px"
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif"
    lineHeight: 1.45
  label:
    fontSize: "12px"
  broadcast-title:
    fontSize: "30px"
    fontWeight: 700
    letterSpacing: "-0.02em"
  broadcast-body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    lineHeight: 1.3
rounded:
  artwork: "8px"
  control: "12px"
  panel: "14px"
  pill: "999px"
spacing:
  small: "8px"
  medium: "12px"
  layout: "16px"
  card: "18px"
  broadcast: "24px"
components:
  button:
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  input:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
    padding: "{spacing.card}"
  broadcast-panel:
    textColor: "{colors.broadcast-text}"
    rounded: "{rounded.panel}"
    padding: "22px 24px"
  broadcast-panel-compact:
    textColor: "{colors.broadcast-text}"
    rounded: "{rounded.control}"
    padding: "16px 18px"
---

# Design System: TipTune

## Overview

**Creative North Star: "TipTune's stream information display"**

The approved incumbent language is dark surfaces, a blue accent, system typography, and restrained broadcast information. The overlay extends that language into transparent OBS composition. This record describes the dashboard primitives it inherits and the finished overlay extension; it does not claim a redesign or audit of the rest of TipTune.

Ground truth: `webui/src/app.css`, `webui/src/components/overlay-settings.css`, `webui/src/overlay/overlay.css`, `OverlaySettings.tsx`, `OverlayCanvas.tsx`, and overlay defaults in `webui/src/overlay/types.ts`. Component and layout guidance below is scoped explicitly where broadcast behavior differs from dashboard behavior.

**Key Characteristics:**

- Dark, gently rounded dashboard surfaces with restrained blue emphasis.
- Grouped broadcast panels on a transparent canvas.
- Bounded song and requester text, with separate placement and readability previews.

## Colors

### Primary

Blue identifies links, selected navigation, save actions, and broadcast status accents. Its weak tint supports selection and informational emphasis.

### Neutral

The dashboard uses `bg` as its page ground, `surface` for cards, and `surface-2` for inputs and nested content. Low-opacity white borders distinguish edges; `text` and `muted` separate primary reading from supporting details.

Broadcast text has its own opaque default. Broadcast accent, text, panel color, and panel opacity are configurable; the default panel uses the incumbent surface color at 90% opacity. Transparency belongs to the canvas, while opacity belongs to its panels. The checkerboard is preview chrome only. Warnings use a warm status dot as a semantic exception, not a second brand accent.

## Typography

System sans-serif is an explicit incumbent choice preserved for this extension. There is no distinct decorative display face or invented type scale.

Dashboard headlines use the recorded headline role; labels and supporting copy remain small. The overlay settings heading is larger than ordinary card headings (18px), with section headings at 16px.

Broadcast hierarchy is separate: Full current-song titles use `broadcast-title`, artists use 21px, and requester names use 15px. Queue titles use 21px, artists 16px, and requesters 14px. Compact current titles are 24px, artists 17px, and queue titles 18px. Broadcast title weight is 700; panel headings and emphasized requesters use 650. These sizes precede broadcast canvas scaling.

**The Bounded Text Rule.** Current titles occupy at most two lines, queue titles one, and alert messages three. Artists and requesters truncate with ellipses; text containers can shrink without forcing page overflow.

## Layout

The incumbent dashboard container is centered with a maximum width of 1120px and page padding of 28px horizontally. Settings use a 16px grid gap and become two columns at 980px. Overlay settings remain bounded by their parent card. Controls auto-fit columns with a 160px minimum; at widths up to 600px they use two shrinkable columns and stack the URL/copy controls. Labels and toolbars wrap.

Broadcast Full panels share a 560px-wide group with 12px gaps; Compact uses 440px with 8px gaps. Alerts precede Now Playing and the queue. The configured corner establishes both the anchor and transform origin. Canvas scaling references 1920 × 1080, respects configured scale and edge margin, and shrinks the complete measured group to fit available width and height. The default corner is bottom left, scale 100%, margin 32, and three upcoming songs.

**The Two Preview Frames Rule.** Full canvas is a 16:9 placement preview with broadcast scaling. Inspect panels uses scale 1, a top-left inset of 20px, and a 440px-high scrollable viewport; its content is 600px wide in Full and 480px in Compact. Horizontal and vertical scrolling stay inside this preview. Inspection height follows the measured group plus 40px. Inspect intentionally ignores the configured broadcast corner and scale without changing either setting.

## Elevation & Depth

The dashboard uses tonal layering, fine borders, and the incumbent `shadow-xs` for cards; `shadow-sm` supports transient dashboard notices. Broadcast panels use a diffuse shadow (0 6px 20px rgba(0,0,0,.24)) to separate information from the scene underneath. No full-canvas opaque backing is added. Exact shadow tokens, focus treatment, and motion are recorded in the sidecar.

## Shapes

Dashboard cards and Full broadcast panels use the panel radius. Inputs, buttons, and Compact panels use the control radius. Broadcast artwork uses the artwork radius and clips genuine cover images; status indicators are circular and dashboard navigation uses pills. Keep these existing forms instead of introducing a new corner language for overlay setup.

## Components

### Dashboard controls

Buttons inherit quiet translucent fills, fine borders, stronger hover borders, and a visible blue keyboard focus outline. Disabled controls fade and retain the resting fill. Inputs use the darker nested surface. The existing sticky Settings Save action remains the authoritative commit action; overlay controls participate in it.

### Broadcast panels

Now Playing, upcoming queue, and transient alerts share the configured panel background, rounded geometry, and shadow. Artwork is 96px square in Full and 64px in Compact, with an inline SVG music-note fallback for missing or failed images. Queue rows use dividers, tabular position numbers, song attribution when known, and a source abbreviation. Panels disappear when their content or visibility setting is absent.

### Preview and notices

Sample content is explicitly illustrative; Live reports connection state. Preview request/warning/notice buttons affect the preview, while Send buttons target the actual OBS display. Alerts enter once with a short upward fade (180ms); Motion Off removes this animation and reduced-motion preference disables overlay animations and transitions. Inspection changes framing only, not the event lifecycle or broadcast settings.

## Do's and Don'ts

### Do:

- **Do** preserve the incumbent dark palette, blue emphasis, and approved system typography.
- **Do** keep placement preview and readable inspection as distinct frames of the same panel content.
- **Do** bound text and keep preview scrolling within the settings card.
- **Do** retain genuine artwork and the inline SVG fallback.

### Don't:

- **Don't** apply inspection coordinates or scale to the broadcast canvas.
- **Don't** put the preview checkerboard or settings chrome into OBS output.
- **Don't** promote overlay-specific panel widths or broadcast type sizes into dashboard-wide rules.
