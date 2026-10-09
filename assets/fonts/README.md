# assets/fonts

Bu klasore OFL lisansli font dosyalari konur (repoya gomulu, apt'e bagimsiz):

- `Inter-Regular.ttf`, `Inter-Bold.ttf` — https://github.com/rsms/inter/releases
- `JetBrainsMono-Regular.ttf`, `JetBrainsMono-Bold.ttf` — https://github.com/JetBrains/JetBrainsMono/releases

Render kodu her zaman `src/devbrief/render/fonts.py` uzerinden yukler.
Dosyalar henuz inmemisse `load_font()` Pillow varsayilanina duser (testler gecer,
gorsel kalite dusuk olur). Gercek video uretmeden once fontlari indir:

```powershell
# Ornek (guncel surum numaralarini release sayfasindan dogrula):
Invoke-WebRequest -Uri "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Regular.otf" -OutFile "assets/fonts/Inter-Regular.ttf"
```

Lisans: OFL — `OFL.txt` dosyasini kaynaktan alip yanina koy.
