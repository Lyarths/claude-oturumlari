# 💬 Claude Oturumları — Çoklu Hesap Destekli Claude.ai Masaüstü Uygulaması

Birden fazla Google/Claude hesabını **tek bir masaüstü penceresinde** birleştiren, hesaplardan çıkış yapmadan veya onlarca Chrome sekmesi arasında kaybolmadan **anında hesap değiştirip doğrudan uygulama içinden Claude ile sohbet etmenizi sağlayan** modern masaüstü istemcisi.

---

## 💡 Neden Bu Uygulama Var?

Claude.ai'yi birden fazla hesapla kullanırken her seferinde farklı Chrome profilleri açmak, hangi hesapta hangi sohbetin kaldığını hatırlamaya çalışmak ve arkada açık kalan onlarca Chrome penceresinin gigabaytlarca RAM tüketmesi büyük bir sorundur.

**Claude Oturumları** bu sorunu kökünden çözer:
- Bilgisayarınızdaki tüm Chrome profillerini (ad, e-posta ve profil fotoğrafıyla) otomatik algılar.
- Her hesap için **uygulama içinde tamamen bağımsız, kalıcı bir Claude.ai oturumu** açar.
- Sol menüden bir hesaba tıkladığınız anda **hiç giriş/çıkış yapmadan** doğrudan o hesabın Claude ekranına geçer ve yazışmaya devam edersiniz!

---

## ✨ Öne Çıkan Özellikler

### 1. 🖥️ Uygulama İçi Tam Teşekküllü Çoklu Claude İstemcisi
- Her profil kendi izole çerez ve depolama alanında (`QWebEngineProfile`) çalışır; hesaplar birbirine karışmaz.
- Sol menüdeki **HESAPLAR** listesinden tek tıkla hesaplar arasında geçiş yapabilir, **SOHBETLER** listesinden o hesaba ait geçmiş konuşmalarınıza anında dönebilir veya **+ Yeni sohbet** başlatabilirsiniz.
- İhtiyaç duyduğunuzda üst bardaki **"Chrome'da aç"** butonuyla o anki sohbeti doğrudan gerçek Chrome profilinde de açabilirsiniz.

### 2. 🧠 Akıllı RAM & CPU Tasarrufu (Sekme Dondurma + Uyku Modu)
- **Otomatik Sekme Yönetimi:** Aktif olarak kullandığınız hesap tam performansla çalışırken, arka plandaki son 3 hesap **Frozen (Dondurulmuş - %0 CPU)** moduna alınır (tıkladığınız anda milisaniyede açılır), daha eski sekmeler ise **Discarded** edilerek RAM'den boşaltılır.
- **Sistem Tepsisi (System Tray) & RAM Sıfırlama:** Pencereyi kapattığınızda sağ alt gizli simgelere küçülür. Sağ tıklayıp **"RAM / Önbelleği Sıfırla (Uyku Modu)"** seçeneğine bastığınızda tüm WebEngine süreçlerini ve Windows Working Set belleğini temizleyerek arka planda neredeyse sıfır RAM ile bekler.

### 3. 🕒 Gerçek Son Mesajlaşma Zamanı & Limit Bildirimi
- Sadece sekmeye tıklamayı değil, gerçekten **mesaj gönderdiğiniz** veya sohbet ettiğiniz zamanı (`SON` rozeti ve *"Şimdi"*, *"Bugün 20:53"* gibi) baz alarak en son kullandığınız hesabı en üstte tutar.
- Bir hesapta mesaj limiti dolduğunda bunu otomatik algılar, hesabın yanında kırmızı uyarı gösterir ve limitin **saat kaçta sıfırlanacağını** (`Limit doldu · açılış 23:00`) hesaplar.

---

## 🔒 Gizlilik & Güvenlik

- Bu repo **hiçbir kişisel veritabanı (`.db`), çerez (`Cookies`), oturum veya geçmiş dosyası içermez.**
- Tüm oturum ve önbellek verileri yalnızca kullanıcının kendi bilgisayarındaki `%LOCALAPPDATA%\ClaudeTakipci` dizininde yerel olarak tutulur.

---

## 🚀 Kurulum ve Çalıştırma

```bash
pip install PyQt6 PyQt6-WebEngine
python app.py
```

- **Hızlı Başlatma:** `Baslat.bat` dosyasına çift tıklayarak konsol penceresi olmadan doğrudan çalıştırabilirsiniz.
- **Masaüstü Kısayolu:** `Masaustune_Kisayol_Ekle.bat` dosyasını bir kez çalıştırarak masaüstünüze ikonlu kısayol ekleyebilirsiniz.
