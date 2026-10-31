"""
The style sheet of a flat page, written into the head of it.

The page is a single file, so the sheet is inline: a second file would have to
be published next to it and cached with it, for a page that is one document of
a folder of wheels. The selectors are the classes the objects of the page write
(see obj_flat), so the two modules are read together.

Usage:
    from alasio.codegen.pypisite.flat import FLAT_STYLE

    with gen.Doc('Wheels', head=[Element(gen, 'style', Raw(gen, FLAT_STYLE, indent=False))]):
        ...
"""

__all__ = [
    'FLAT_STYLE',
]

FLAT_STYLE = """\
:root { --fg:#1f2328; --muted:#59636e; --faint:#8c959f; --line:#d1d9e0; --soft:#e6eaef;
        --bg:#fff; --head:#f6f8fa; --accent:#0969da; --pure:#dafbe1; --pure-line:#aceebb;
        --pure-fg:#116329; --compiled:#ddf4ff; --compiled-line:#b6e3ff; --compiled-fg:#0550ae;
        --abi3:#8250df; }
* { box-sizing:border-box; }
body { margin:0; padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
       font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main, header, nav, footer { max-width:1180px; margin:0 auto; }
h1 { font-size:2rem; margin:0 0 .25rem; letter-spacing:-.02em; }
h2 { font-size:1.35rem; margin:2.5rem 0 .4rem; padding-bottom:.3rem; border-bottom:1px solid var(--line); }
h3 { font-size:1.05rem; margin:1.6rem 0 .2rem; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
code, pre, .plat { font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace; }
pre { background:var(--head); border:1px solid var(--line); border-radius:6px; padding:.6rem .8rem;
      overflow-x:auto; font-size:.85rem; }
.lede { color:var(--muted); margin:.2rem 0 1rem; }
.badge { display:inline-block; font-size:.7rem; font-weight:600; padding:.05rem .4rem;
         border:1px solid var(--line); border-radius:999px; color:var(--muted); vertical-align:.12em; }
.badge.pure { background:var(--pure); border-color:var(--pure-line); color:var(--pure-fg); }
.badge.compiled { background:var(--compiled); border-color:var(--compiled-line); color:var(--compiled-fg); }
.meta { color:var(--muted); font-size:.85rem; margin:.1rem 0 .65rem; }
.anchor { color:var(--faint); opacity:.45; font-weight:400; margin-left:.25rem; }
.anchor:hover { opacity:1; }
.toc { list-style:none; padding:0; margin:.4rem 0 0; }
.toc li { display:block; margin:.25rem 0; }
.toc .versions { color:var(--muted); font-size:.82rem; margin-left:.45rem; }
.toc .versions a { color:var(--muted); }
.toc .versions a:hover { color:var(--accent); }
.release-tag { font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;
               font-size:.78rem; font-weight:400; color:var(--muted); margin-left:.4rem; }
.release-tag:hover { color:var(--accent); }
.table-wrap { width:fit-content; max-width:100%; overflow-x:auto;
              border:1px solid var(--line); border-radius:6px; }
table.matrix { border-collapse:collapse; table-layout:auto; width:max-content; font-size:.8rem; }
table.matrix th, table.matrix td { border-right:1px solid var(--soft); border-bottom:1px solid var(--soft);
                                   padding:.3rem .5rem; text-align:center; white-space:nowrap; }
table.matrix th:first-child { width:320px; padding:0; }
table.matrix th:last-child, table.matrix td:last-child { border-right:0; }
table.matrix tbody tr:last-child th, table.matrix tbody tr:last-child td { border-bottom:0; }
table.matrix thead th { background:var(--head); font-weight:600; font-size:.78rem; }
table.matrix tbody th { background:#fbfcfd; text-align:left; font-weight:500; white-space:normal;
                        overflow-wrap:anywhere; }
table.matrix td.na { background:#fafbfc; }
table.matrix td.pure { background:var(--pure); }
/* the version column of a pure table is a label, not the wide platform column
   of a matrix: it takes the width of the version and centres it */
table.matrix.pure th:first-child { width:auto; padding:.3rem .75rem; }
table.matrix.pure tbody th { text-align:center; }
.platform-head { display:block; box-sizing:border-box; width:320px; padding:.3rem .5rem; font-size:.78rem; }
.plat { display:block; box-sizing:border-box; width:320px; padding:.3rem .5rem;
        font-size:.75rem; line-height:1.3; overflow-wrap:anywhere; word-break:break-word; }
.abi3 { font-size:.64rem; color:var(--abi3); margin-left:.2rem; }
.note { color:var(--muted); font-size:.8rem; margin:.45rem 0 0; }
footer { margin-top:3rem; padding-top:1rem; border-top:1px solid var(--line); color:var(--faint); font-size:.8rem; }
@media (max-width:640px) { body { padding:1.25rem .75rem 3rem; } }
"""
