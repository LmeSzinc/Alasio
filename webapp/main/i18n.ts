interface TrayTranslations {
  show: string;
  hide: string;
  exit: string;
}

const trayTranslations: Record<string, TrayTranslations> = {
  'en-US': { show: 'Show', hide: 'Hide', exit: 'Exit' },
  'zh-CN': { show: '显示', hide: '隐藏', exit: '退出' },
  'zh-TW': { show: '顯示', hide: '隱藏', exit: '退出' },
  'ja-JP': { show: '表示', hide: '非表示', exit: '終了' },
  'es-ES': { show: 'Mostrar', hide: 'Ocultar', exit: 'Salir' },
};

export function getTrayTranslations(lang: string): TrayTranslations {
  return trayTranslations[lang] || trayTranslations['en-US'];
}
