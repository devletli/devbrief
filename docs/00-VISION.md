# Vizyon ve Konumlandırma

**Ürün:** DevBrief TR — yazılımcılar için günlük 45 saniyelik "bugün ne değişti, sana ne etkisi var" videosu.

## Hedef kitle
Türkçe konuşan yazılımcılar, DevOps/SRE, teknik yöneticiler, CS öğrencileri. İngilizce haberleri takip etmeye zamanı olmayanlar.

## Fark yaratan 4 şey
1. **Kürasyon:** Günde 100+ girdiden 1-3 tane. "Her şeyi özetleme" tuzağına düşme.
2. **Etki açısı:** "Ne oldu" değil "senin projeni nasıl etkiler, ne yapmalısın".
3. **Doğruluk:** Doğrulama aşaması + insan onayı + kaynak linki.
4. **Türkçe niş:** Rekabet İngilizcede yoğun, Türkçede az.

## Başarı ölçütleri (ilk 90 gün)
| Ölçüt | Hedef |
|---|---|
| Yayın sıklığı | Haftada 5 video (hafta içi her gün) |
| Hatalı bilgi düzeltmesi | 0 (olursa 24 saatte düzeltme) |
| Onay başına süre | < 5 dk/video |
| 30. günde ortalama izlenme/video | Ölç, sonra hedef koy (varsayım yapma) |
| Pipeline başarı oranı (hatasız çalışan gün) | > %90 |

## Kapsam dışı (şimdilik)
Tam otomatik yayın, canlı yayın, yüz/avatar videoları, çok dilli çıktı, ücretli abonelik.

## Riskler
| Risk | Etki | Önlem |
|---|---|---|
| Model uydurması | İtibar kaybı | Doğrulama aşaması, insan onayı |
| "Toplu üretilmiş" içerik algısı | Erişim/monetizasyon kaybı | Özgün kürasyon, kendi yorumun, çeşitli şablon |
| Ücretsiz modellerin değişmesi/kalkması | Pipeline kırılır | Model listesi config'te, fallback zinciri |
| YouTube API kısıtı | Yükleme yapılamaz | Doğrulanmamış API projelerinde yüklenen videolar özel (private) kilitlenebilir; denetim (audit) başvurusu yap, o zamana kadar elle yükle |
| Kaynak site koşulları/telif | Hukuki | Sadece başlık+kısa özet+link; tam metin kopyalama yok; RSS/API tercih |
| Tek kişilik bakım yükü | Tükenmişlik | Haftalık 2 saat bakım bütçesi, basit mimari |
