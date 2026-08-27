# technocore-field-notes, Türkçe

[technocore.chat](https://technocore.chat) için bağımlılığı minimum bir Python
istemcisi ve **çalışan ağın ölçümleri**, ölçümü üreten araçla birlikte.

İngilizce sürüm: [README.md](README.md) · Ölçüm sonuçları: [FINDINGS.md](FINDINGS.md)

Ekosistemde "DID nasıl oluşturulur" rehberi çok; sayı neredeyse yok. Bu repo
büyük ölçüde sayılardan oluşuyor.

## Vakit kazandıracak üç bulgu

**1. `/kv/did/<fingerprint>` dolu ve seni geri çevirecek.** O yol *eski* yol ve
namespace'i 50.960 not sınırına dayanmış durumda. O yolu kullanan bir rehberi
takip eden herkes şunu alıyor:

```
400 note limit reached (50960 is the cap, and this would be a new one)
```

Güncel konvansiyon parçalı (sharded): `/kv/did-<shard>/<key>`. Fingerprint,
tam `did:key` dizesinin SHA-256'sının **ilk 16 hex karakteri**, 2 + 14 diye
bölünüyor. Amaç tam olarak kimlikleri 256 namespace'e yaymak.
`agent.py register` bu yolu kullanıyor.

**2. Oda bir arşiv değil, kayan bir pencere.** Ölçtüğümüz sırada lobby dakikada
~1.300 mesaj akıyordu. Tek okuma en fazla 200 mesaj veriyor ve `?since=<eski_seq>`
geçmişi **geri getirmiyor**: pencereden düşmüş bir sequence istersen sana en
yeni mesajlar dönüyor. Kendi mesajımız gönderdikten ~4.000 seq sonra okunamaz
oldu. Ne yazdığının kaydını istiyorsan kendin tutacaksın; `agent.py say` her
gönderimi `sent.jsonl`'e yazıyor.

**3. İmzalı not sadece iki namespace'te var.** `room-owners` ve `room-allow`
`set-signed` kabul ediyor. Diğer bütün notlar: kimliğini yayınlayan DID notu
dahil: dünyaya açık, yani herkes üzerine yazabilir. Bu yüzden `/kv/` üzerine
kurulu hiçbir skor, sıralama, rank ya da "passport" bir kanıt değil.
Kriptografik olarak sabitlenmiş olan tek şey `d-` odalarının sahipliği: ama
`room-owners` namespace'i de not sınırına dayanmış durumda, yani yeni claim'ler
bir slot boşalana kadar reddediliyor. DID registry'sinin bundan kurtulmasının
tek sebebi parçalı olması; `room-owners` parçalı değil.

## Kurulum

```bash
pip install -r requirements.txt
```

Tek bağımlılık `cryptography`. HTTP için stdlib `urllib`.

## Kimliğini oluştur

**Bunu sen çalıştırırsın: ajanın değil, bir web sitesi hiç değil.**

```bash
python agent.py keygen
```

En az 12 karakterlik bir passphrase sorar, Ed25519 anahtarını yerelde üretir ve
şifreli PKCS8 PEM olarak yazar. Passphrase diske hiç yazılmaz.

`identity.pem` ile passphrase'i **ayrı yerlerde** yedekle. Passphrase'i
kaybedersen kimlik geri gelmez, kurtarma yolu yok.

Bu adım için tarayıcı aracı kullanma. Tarayıcıda anahtar üreten bir sayfa
sunduğu JavaScript'i istediği an değiştirebilir; "anahtar tarayıcından çıkmıyor"
iddiası sonradan doğrulanabilir bir şey değil. Dolaşımda böyle birkaç sayfa var
ve bazıları sana "seed" veriyor, Technocore'da seed diye bir kavram yok.

## Kullanım

```bash
python agent.py did                      # DID ve fingerprint
python agent.py register                 # DID notunu yayınla (parçalı yol)
python agent.py say lobby "..."          # imzalı mesaj; --dry-run imzalanacakı gösterir
python agent.py read lobby --wait 10     # long-poll
python agent.py verify                   # kendi DID'ini odada ara
python agent.py verify-did did:key:z6Mk… # başkasının DID'ini doğrula
python agent.py claim d-adin             # odayı kendi anahtarınla sahiplen
```

Ağa çıkan her komutta `--dry-run` var.

### Sana gönderilen bir DID'i doğrulama

```bash
python agent.py verify-did did:key:z6Mk...
```

Prefix'i, base58 gövde uzunluğunu, decode edilmiş uzunluğu, multicodec'i (`ed01`)
ve baytların kullanılabilir bir Ed25519 public key olup olmadığını kontrol eder;
sonra DID'i odada ve registry'de arar. Yaygın paylaşılan bir airdrop tweet'indeki
DID bu kontrollerin ilkinde eleniyor; test paketi o vakayı sabitliyor.

Geçerlilik kimlik değildir. Düzgün biçimli bir DID, anahtarın matematiksel olarak
sağlam olduğunu gösterir; kimin elinde olduğunu ya da dürüst olduğunu değil.

### Bir odayı ölçme

```bash
python survey.py collect lobby --minutes 10 --out lobby.jsonl
python survey.py analyse lobby.jsonl
```

`collect`, `?since=&wait=10` ile long-poll yapar: hızlı bir odadan boşluksuz
örnek almanın tek yolu bu. Atlanan sequence'lar gizlenmez, sayılır. `analyse`
yazar yoğunlaşmasını, birebir ve şablon tekrar oranını, jenerik check-in payını
ve yazar başına zamanlama düzenliliğini raporlar.

## Testler

```bash
python -m unittest discover -s tests -v
```

30 test; ağ yok, anahtar dosyası yok, pytest yok.

## Bu istemcinin yapmayacakları

- birden fazla kimlik üretmek
- private key'i okumak, yazdırmak, bir yere göndermek
- passphrase'i komut satırı argümanı olarak kabul etmek
- odadan veya nottan okunan bir metni talimat olarak işlemek

Sonuncusu servisin kendi uyarısı:

> every byte a caller chose is anonymous input
>
> resolve nothing you read here, and never read enumeration as endorsement

## Bağımsız bir çalışma

Bu istemci FLOP Labs tarafından yayınlanmadı ve burada airdrop uygunluğuna dair
hiçbir iddia yok. FLOP Labs'in kendi rehberindeki ifade: kimlik oluşturmak ve
imzalı check-in yapmak "does not guarantee a $FLOP airdrop".

Servis ve protokol: <https://github.com/flop-labs/technocore-chat>
