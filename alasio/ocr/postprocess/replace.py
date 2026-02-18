def replace_dash(text, to='-', punct=True, box=True, cn=False, jp=False, kr=False):
    """
    Replaces dash-like characters with a target string/character based on categories.

    1. General Punctuation & Math (punct=True)
    | Char | Unicode | Description |
    |------|---------|-------------|
    | ‐    | U+2010  | Hyphen |
    | ‑    | U+2011  | Non-breaking hyphen |
    | ‒    | U+2012  | Figure dash |
    | –    | U+2013  | En dash |
    | —    | U+2014  | Em dash |
    | ―    | U+2015  | Horizontal bar |
    | ⁓    | U+2053  | Swung dash |
    | −    | U+2212  | Minus sign |
    | ⁃    | U+2043  | Hyphen bullet |
    | ⎯    | U+23AF  | Horizontal line extension |
    | －    | U+FF0D  | Full-width Hyphen-minus |
    | ￣    | U+FFE3  | Full-width Macron |

    2. Box Drawing (box=True)
    | Char | Unicode | Description |
    |------|---------|-------------|
    | ─    | U+2500  | Box drawings light horizontal |
    | ━    | U+2501  | Box drawings heavy horizontal |
    | ┄    | U+2504  | Box drawings light quadruple dash horizontal |
    | ┅    | U+2505  | Box drawings heavy quadruple dash horizontal |
    | ┈    | U+2508  | Box drawings light triple dash horizontal |
    | ┉    | U+2509  | Box drawings heavy triple dash horizontal |
    | ╌    | U+254C  | Box drawings light double dash horizontal |
    | ╍    | U+254D  | Box drawings heavy double dash horizontal |
    | ╴    | U+2574  | Box drawings light left |
    | ╸    | U+2578  | Box drawings heavy left |

    3. Chinese Characters & Components (cn=False)
    | Char | Unicode | Description |
    |------|---------|-------------|
    | 一    | U+4E00  | CJK Ideograph 'One' |
    | ⼀    | U+2F00  | Kangxi Radical 'One' |
    | ㇐    | U+31D0  | CJK Stroke 'Heng' (Horizontal) |
    | ㄧ    | U+3127  | Bopomofo Letter 'I' (Horizontal in landscape) |

    4. Japanese Characters (jp=False)
    | Char | Unicode | Description |
    |------|---------|-------------|
    | ー    | U+30FC  | Katakana-Hiragana prolonged sound mark |

    5. Korean Characters (kr=False)
    | Char | Unicode | Description |
    |------|---------|-------------|
    | ㅡ    | U+3161  | Hangul vowel 'EU' |
    """
    chars = ""
    if punct:
        chars += "\u2010\u2011\u2012\u2013\u2014\u2015\u2053\u2212\u2043\u23AF\uFF0D\uFFE3"
    if box:
        chars += "\u2500\u2501\u2504\u2505\u2508\u2509\u254C\u254D\u2574\u2578"
    if cn:
        chars += "\u4E00\u2F00\u31D0\u3127"
    if jp:
        chars += "\u30FC"
    if kr:
        chars += "\u3161"

    if not chars:
        return text
    table = str.maketrans({c: to for c in chars})
    return text.translate(table)
