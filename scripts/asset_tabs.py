"""Build the two-tab asset tracking page."""

from html import escape


def build_asset_page(gold_html: str, etf_html: str) -> str:
    """Return a self-contained shell containing the two complete child pages."""
    gold_srcdoc = escape(gold_html, quote=True)
    etf_srcdoc = escape(etf_html, quote=True)

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>资产跟踪</title>
  <style>
    * {{ box-sizing: border-box; }}
    html {{ overflow-x: hidden; background: #f4f4ee; }}
    body {{ margin: 0; min-width: 0; color: #18312b; background: #f4f4ee; font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }}
    .asset-nav {{ position: sticky; top: 0; z-index: 10; height: 64px; display: flex; align-items: center; gap: 22px; padding: 0 max(14px, calc((100% - 1180px) / 2)); border-bottom: 1px solid #d7ddd4; background: rgba(255, 254, 250, .96); }}
    .asset-title {{ flex: 0 0 auto; margin: 0; color: #18312b; font-size: 15px; font-weight: 720; letter-spacing: .02em; }}
    .asset-tabs {{ display: flex; align-self: stretch; gap: 4px; min-width: 0; }}
    .asset-tab {{ position: relative; min-width: 76px; border: 0; padding: 0 13px; color: #68746f; background: transparent; font: inherit; font-weight: 650; cursor: pointer; }}
    .asset-tab:hover {{ color: #18312b; }}
    .asset-tab[aria-selected="true"] {{ color: #176c56; }}
    .asset-tab[aria-selected="true"]::after {{ content: ""; position: absolute; right: 12px; bottom: 0; left: 12px; height: 2px; border-radius: 2px 2px 0 0; background: #176c56; }}
    .asset-tab:focus-visible {{ outline: 3px solid rgba(23, 108, 86, .22); outline-offset: -4px; border-radius: 6px; }}
    .asset-panel {{ width: 100%; min-width: 0; overflow: hidden; }}
    .asset-panel[hidden] {{ display: none; }}
    .asset-frame {{ display: block; width: 100%; min-width: 0; height: 1px; border: 0; overflow: hidden; background: #f4f4ee; }}
    @media (max-width: 520px) {{
      .asset-nav {{ gap: 8px; padding-inline: 8px; }}
      .asset-title {{ padding-left: 4px; font-size: 13px; }}
      .asset-tab {{ min-width: 68px; padding-inline: 8px; }}
      .asset-tab[aria-selected="true"]::after {{ right: 8px; left: 8px; }}
    }}
  </style>
</head>
<body>
  <header class="asset-nav">
    <p class="asset-title">资产跟踪</p>
    <div class="asset-tabs" role="tablist" aria-label="资产类别">
      <button class="asset-tab" id="tab-gold" type="button" role="tab" aria-selected="true" aria-controls="panel-gold" tabindex="0" data-asset="gold">黄金</button>
      <button class="asset-tab" id="tab-us-etf" type="button" role="tab" aria-selected="false" aria-controls="panel-us-etf" tabindex="-1" data-asset="us-etf">美股 ETF</button>
    </div>
  </header>
  <main>
    <section class="asset-panel" id="panel-gold" role="tabpanel" aria-labelledby="tab-gold">
      <iframe class="asset-frame" id="frame-gold" title="黄金跟踪" scrolling="no" srcdoc="{gold_srcdoc}"></iframe>
    </section>
    <section class="asset-panel" id="panel-us-etf" role="tabpanel" aria-labelledby="tab-us-etf" hidden>
      <iframe class="asset-frame" id="frame-us-etf" title="美股 ETF 跟踪" scrolling="no" srcdoc="{etf_srcdoc}"></iframe>
    </section>
  </main>
  <script>
    (() => {{
      const ids = ["gold", "us-etf"];
      const tabs = ids.map((id) => document.getElementById(`tab-${{id}}`));
      const panels = ids.map((id) => document.getElementById(`panel-${{id}}`));
      const frames = ids.map((id) => document.getElementById(`frame-${{id}}`));
      const watched = new WeakSet();

      const fitFrame = (frame) => {{
        const doc = frame.contentDocument;
        const panel = frame.closest(".asset-panel");
        if (!doc || !doc.body || !panel || panel.hidden) return;
        const styles = frame.contentWindow.getComputedStyle(doc.body);
        const marginTop = parseFloat(styles.marginTop) || 0;
        const marginBottom = parseFloat(styles.marginBottom) || 0;
        const height = Math.ceil(doc.body.getBoundingClientRect().height + marginTop + marginBottom);
        if (height > 0 && Math.abs(frame.getBoundingClientRect().height - height) > 1) {{
          frame.style.height = `${{height}}px`;
        }}
      }};

      const watchFrame = (frame) => {{
        const doc = frame.contentDocument;
        if (!doc || watched.has(doc)) return;
        watched.add(doc);
        fitFrame(frame);
        const refit = () => requestAnimationFrame(() => fitFrame(frame));
        doc.addEventListener("change", refit);
        doc.addEventListener("toggle", refit, true);
        // srcdoc resolves fragment links against the parent URL; handle them locally.
        doc.addEventListener("click", (event) => {{
          refit();
          const link = event.target.closest("a[href^='#']");
          if (!link) return;
          event.preventDefault();
          const target = doc.getElementById(link.getAttribute("href").slice(1));
          if (!target) return;
          if (target.tagName === "DETAILS") target.open = true;
          fitFrame(frame);
          requestAnimationFrame(() => {{
            const top = window.scrollY + frame.getBoundingClientRect().top + target.getBoundingClientRect().top - 76;
            window.scrollTo({{ top: Math.max(0, top), behavior: "smooth" }});
          }});
        }});
      }};

      const activate = (id, updateHash = true, focus = false) => {{
        const index = ids.includes(id) ? ids.indexOf(id) : 0;
        tabs.forEach((tab, itemIndex) => {{
          const active = itemIndex === index;
          tab.setAttribute("aria-selected", String(active));
          tab.tabIndex = active ? 0 : -1;
        }});
        panels.forEach((panel, itemIndex) => {{ panel.hidden = itemIndex !== index; }});
        if (focus || updateHash) tabs[index].focus({{ preventScroll: true }});
        requestAnimationFrame(() => {{
          fitFrame(frames[index]);
          if (updateHash) window.scrollTo({{ top: 0, behavior: "auto" }});
        }});
        if (updateHash && location.hash !== `#${{ids[index]}}`) {{
          history.replaceState(null, "", `#${{ids[index]}}`);
        }}
      }};

      tabs.forEach((tab, index) => {{
        tab.addEventListener("click", () => activate(ids[index]));
        tab.addEventListener("keydown", (event) => {{
          let next = index;
          if (event.key === "ArrowRight") next = (index + 1) % ids.length;
          else if (event.key === "ArrowLeft") next = (index - 1 + ids.length) % ids.length;
          else if (event.key === "Home") next = 0;
          else if (event.key === "End") next = ids.length - 1;
          else return;
          event.preventDefault();
          activate(ids[next], true, true);
        }});
      }});

      frames.forEach((frame) => {{
        frame.addEventListener("load", () => watchFrame(frame));
        if (frame.contentDocument && frame.contentDocument.readyState === "complete") {{
          watchFrame(frame);
        }}
      }});
      window.addEventListener("resize", () => frames.forEach(fitFrame));
      window.addEventListener("hashchange", () => activate(location.hash.slice(1), false));
      activate(location.hash.slice(1), false);
    }})();
  </script>
</body>
</html>'''
