# T1579 juan 72: open findings of the unsealed publication

Facts only. Generated on 2026-11-04 from the stored review answers of audit job `20261104-193950-audit-744f` (no model call).

## What was published

- The owner decided on 2026-11-04: publish the juan 72 rewrite as it is and fix the findings later.
- `translations/T1579-072-baihua.md` is the candidate of job `20261104-193950-audit-744f` with paragraph ids added (`publish_unsealed.py` in this folder). Its page and `docs/T1579/search.json` were rebuilt.
- The juan has no seal (no attestation): the audit verdict is `fail`.
- The 28 paragraph ids of the old text stay; the 51 new paragraphs got new ids (79 in all).
- The mechanical checks pass: line ranges of the juan and the glossary check (0 issues).

## Counts

| Kind | Count |
|---|---|
| Blocking findings (gate option b) | 22 |
| `review_doctrine` | 9 |
| `review_terms` | 13 |
| Glossary check | 0 |
| Severity med / low | 8 / 14 |
| Glossary backlog (warnings, not blocking) | 45: `glossary_provenance` 22, `term_binding` 23 |

The glossary backlog is the `glossary_backlog` list of `audits/20261104-193950-audit-744f/T1579-072-audit-report.json`. Its items ask for changes of `translations/glossary/T1579-terms.json` or of `term_occurrences`, not of the reader's text.

## N1: the translation notes show pipeline language

Not a reviewer finding. The fixer wrote its working notes into the `notes` of each section, and the page shows them in the `translation-note` box of each section.

- Note text on the page: 297 characters before, 4939 after.
- The new notes use words that a reader does not know: glossary, plain, `term_id`, clause, inferred, proposed, finding, pipeline. Examples:
  - 「術語 ID：本次輸入沒有提供 glossary 的正式 term_id。」
  - 「建議 pipeline 一併修訂 glossary:輕安 的 plain，修訂後依新 plain 回填。」
  - 「建議把「念住」「五事」列入 glossary。」
- Fix: rewrite the notes of the 7 sections for readers, or move the maintainer parts to the glossary backlog.

## Blocking findings

Ids: `D` = `review_doctrine`, `T` = `review_terms`. Category and severity as the reviewer gave them. Claim and required change are the reviewer's words.

### T1579-072-001: 01 卷題與種性發心決擇

**D1** · `review_doctrine` · `speaker_attribution` · med

- Claim: 「廣決擇文，更不復現」是在說文本本身的狀態：詳細的分判文字在這裡沒有出現。這句話可能是論主自己說的，也可能是譯場或傳本留下的說明，本次資料無法判定。但 notes 直接寫「這是論文本身對文本狀態的聲明」，structured_contract 的 speakers 也只把全段歸給論主，沒有另外處理這一句。這等於在沒有證據的情況下認定了說話者，而且和同一條 note 後面「不作推斷」的立場互相矛盾。正文的直譯「這裡不再出現」本身沒有問題。
- Required change: 修改相關 note：刪除「這是論文本身對文本狀態的聲明」這類斷定，改為說明這句話的說話者（論主本人，或是譯者／傳本的說明）依本次資料無法確定。speakers／references 也要標明這一句的歸屬尚未判定，不能預設為論主的話。

**D2** · `review_doctrine` · `gloss_overreach` · low

- Claim: 「或怖退轉」列在王難、賊難、鬼難、活命、利養恭敬等世俗動機之中，原文沒有說明退轉是從什麼退失。譯文的〔從修行的道路上退失〕把它限定為修道上的退轉，這是原文沒有的解釋。何況「害怕修道退失」很難算是不清淨的動機，這樣解釋可能讓讀者誤解這一項為什麼被歸入不清淨發心。
- Required change: 把〔〕內的解釋改成不指定退失對象的中性說法（例如只解釋為「退失、衰退」），或者刪除這個〔〕。如果要提出「修道退失」的讀法，應放入 notes 並標明是推測，不可寫在正文中當作唯一解釋。

**T1** · `review_terms` · `first_occurrence_format` · med

- Claim: 「貪瞋癡」是本卷首次出現的煩惱術語（三種根本煩惱），譯文只拆寫為「貪、瞋、癡」，沒有按「玄奘原詞〔白話解釋〕」格式加注；同句的「纏」有〔〕，貪、瞋、癡卻沒有，一般讀者無從理解。
- Required change: 在首次出現處為貪、瞋、癡補上〔〕白話解釋（例如分別說明貪愛、瞋恚、愚癡），解釋只依字義，不加入本段以外的義理。

**T2** · `review_terms` · `term_rendering` · low

- Claim: 原文術語寫作「決擇」，譯文的〔〕卻寫成「抉擇」，同一段裡「決擇」和「抉擇」兩種字形並用，讀者可能誤以為是兩個不同概念。
- Required change: 把〔〕裡的「抉擇」統一改為「決擇」，或改用不含這個詞的白話（如「分辨判定」），避免與術語字形混用。

**T3** · `review_terms` · `first_occurrence_format` · low

- Claim: 「菩提」在譯文中先出現在「迴向菩提〔…趣求菩提覺悟〕」以及發心的〔〕裡，都沒有單獨解釋；要到最後「趣證菩提」才首次加上〔覺悟〕，首次解釋的位置太晚。
- Required change: 把「菩提〔覺悟〕」的白話解釋移到譯文中菩提首次出現的位置（或在迴向菩提的〔〕裡明確說明菩提即覺悟），後面再出現時不必重複加注。

### T1579-072-002: 02 自利利他品決擇

**D3** · `review_doctrine` · `命題框架增譯` · low

- Claim: 原文「依於自義及與他義」是說九種正行本身以自義與他義為所依。譯文加上「而說的」，就把它改成「這種分類是依自他利益來說的」這樣一句關於言說的話。這改變了命題的主詞與層次，原文沒有這個言說框架。
- Required change: 刪去「而說的」這個言說框架，讓九種正行本身「依於」自義與他義。

**D4** · `review_doctrine` · `語氣增譯／條件關係` · low

- Claim: 第二種無堪任性的原文把「相續已熟」與「於此無間造作積集能障正加行業」直接並列，沒有轉折詞。譯文加了「卻」，等於斷定兩個條件之間有轉折。這和 notes 對第三種「照原文並列、不加讓步語氣」的處理原則不一致，也在正文裡預設了原文沒有說明的邏輯關係。
- Required change: 去掉正文裡的轉折詞「卻」，改成和原文一樣的並列連接。如果認為需要說明兩者的關係，應放進〔〕並標明是推定。

**D5** · `review_doctrine` · `預設增譯` · low

- Claim: 原文「無匱」只是正面描述「沒有匱乏」。譯文作「不再匱乏」，「再」字預設此人原本處於匱乏，原文沒有這一層，屬於帶預設的增義。
- Required change: 把「不再匱乏」改成不帶先前狀態預設的「沒有匱乏」之類的表述，與 negation_scope 中「無匱」只否定「匱」的說明一致。

**T4** · `review_terms` · `first_occurrence_format` · low

- Claim: 「增上慧」在 term_occurrences 已經綁成術語 zeng_shang_hui，但譯文第一次出現時沒有照「玄奘原詞〔白話解釋〕」的格式附上解釋。後面毘鉢舍那的〔〕裡雖然提到「增上慧」，只是重複原詞，不算解釋。如果本卷前文沒有出現過這個詞，就不合首次格式。
- Required change: 先確認本卷前文是否已有「增上慧」的首次解釋。如果沒有，在第一次出現處補上〔〕白話解釋，例如說明它是更高層次的智慧（以 glossary 或既有 evidence 為準）。

### T1579-072-003: 03 真實義五事總標

**D6** · `review_doctrine` · `增譯／義理解讀` · med

- Claim: 原文只並列「言說隨眠」「言說隨覺」兩個因緣，沒有說兩者構成「潛伏／現行」的對照。譯文的〔〕把這組對照寫成原文的結構，還把「隨覺」解成「言說隨之覺起、現行」，等於讓「言說」本身當作現起的主語。「隨覺」的字面是隨著言說而覺了，也就是認知跟隨言說，並不是言說本身現行。這組現行對照是譯者補入的義理，和 note 自己說的「原文對這兩項沒有進一步解釋」不一致。
- Required change: 兩項的〔〕只保留字面義，例如「隨眠」取潛伏未現的意思，「隨覺」取隨著言說而覺了的意思。刪除正文〔〕中「與下一項成對」「與上一項的潛伏相對」「現行」這些原文沒有的結構性解讀；如果要保留這種解讀，請移到 notes 或 variants，並標明是詮釋。

**T5** · `review_terms` · `term_rendering` · med

- Claim: 原文「說之為假」的「假」對應同一段問答中的「假有」（答案是「當言俱有」，也就是實有、假有兩種都有）。譯文寫成「假說」，切斷了和「假有」這個術語的對應，現代讀者還可能把它讀成「假設、假說（hypothesis）」。
- Required change: 改譯為「那就說它是假有」之類的寫法，明確對應「假有」，並和前文「假有〔假名安立之法〕」保持一致。

### T1579-072-004: 04 五事生起與不一不異

**D7** · `review_doctrine` · `命題邏輯／因果方向` · med

- Claim: 原文「誹謗蘊中，善、無記法不清淨過」的意思是：如果說取（欲貪）與蘊不異，就等於把蘊中的善法、無記法也判為不清淨，這種判定本身就是誹謗，所以構成不清淨的過失。譯文寫成「會有誹謗……蘊中善法、無記法的過失，使它們成為不清淨」，變成「誹謗」讓善法、無記法變得不清淨，弄錯了因果方向。原文的「不清淨」是誹謗的內容，也就是誤加在善法、無記法上的判定，不是誹謗造成的結果。此外，原文的「誹謗……不清淨過」和下句「增益常性不清淨過」句構平行，兩者都是「不清淨過」這一類過失；譯文把前一句改成另一種句構，讀者就看不出兩邊過失是對稱的。
- Required change: 改譯這一句，說明：取與蘊如果不異，蘊中的善法、無記法就會被判為和欲貪一樣不清淨，這是誹謗，屬於不清淨的過失。刪去「使它們成為不清淨」這種表示誹謗造成結果的說法，並讓這一句和下句「增益常性的不清淨過失」保持平行的句構。

**D8** · `review_doctrine` · `否定範圍／次第` · low

- Claim: 原文「顯示不異亦非不異」的次第是：先說「不異」，即否定「異」；再說「非不異」，即否定「不異」。structured_contract 的 negation_scope 也是這樣標注。譯文卻寫成「既不是不異、也不是異」，把兩個命題的先後對調了。這和同段後文「相與真如不異、也非不異」的譯法不一致，也和 negation_scope 的標注對不上，讀者難以把譯文對回原文的兩個否定。
- Required change: 依原文次第改譯，先說「不異（不是異）」，再說「也非不異」，讓譯文與 negation_scope 標注、後文「不異、也非不異」的譯法一致。

**T6** · `review_terms` · `first_occurrence_format` · med

- Claim: 「麁重〔粗重性〕」首次出現時，〔〕只是把原詞換成通行字再加上「性」，等於重複原詞，沒有給一般讀者真正的白話解釋。這裡的麁重與輕安成對，指身心沉重、不堪任運修行的狀態，「粗重性」反而容易被讀成「粗糙」。譯者在 notes 裡用「沒有給一般讀者真正的白話解釋」為理由擴寫了輕安的〔〕，卻沒有同樣處理麁重，前後標準不一致。
- Required change: 把〔〕改寫成實質白話解釋，例如「身心沉重、僵滯，不堪任修行的狀態，與輕安相對」；因為這會偏離 glossary plain「粗重性」，所以要在 notes 說明理由，並與輕安的處理方式一致。

### T1579-072-005: 05 五事行相與差別名

**D9** · `review_doctrine` · `增譯／括注義理超出原文` · low

- Claim: 原文在這裡只把「戲論」列為相的差別名稱，沒有另外說明。正文括注卻加了兩個原文沒有的命題：一是用「虛妄分別所戲論、所呈現出來的相」解釋戲論，這是拿「戲論」來解釋「戲論」，形成循環定義，也是譯者自己的詮釋；二是加了否定性判斷「不等同於分別本身」。這兩點都不是原文命題，而且與 glossary 對「戲論」的 plain（「虛妄分別」）和 rule（無始虛妄分別名言）直接衝突。為了區分相與分別這兩事而作的說明屬於結構分析，應放在 notes（目前 notes 已有相同說明），不應以正文括注的形式出現，讓讀者誤以為這是原文本身的論斷。
- Required change: 正文括注只保留簡短、不帶論斷的白話解釋，並與 glossary 協調。把「此處列為相的差別名稱」「不等同於分別本身」這類結構性、對比性的說明移到 notes（現有 notes 第四條已涵蓋）。刪除「虛妄分別所戲論」這種循環式的詮釋性補文。

**T7** · `review_terms` · `first_occurrence_format` · low

- Claim: 戲論首次出現的〔〕用「所戲論」來解釋「戲論」，是循環解釋，一般讀者仍然看不懂戲論本身的白話意思。
- Required change: 把〔〕內的「所戲論」改成不重複原詞的白話說法（例如：由虛妄分別、名言造作而呈現的相）。保留「此處列為相的差別名稱，不等同於分別本身」的區分，不另外增加義理。

### T1579-072-006: 06 五事攝屬諸門

**T8** · `review_terms` · `first_occurrence_format` · med

- Claim: 四依（法依、義依、了義經依、智依）是專門術語，本段首次出現時沒有依「玄奘原詞〔白話解釋〕」格式附上白話。一般讀者無法從「法依」「義依」看出意思。
- Required change: 在法依、義依、了義經依、智依首次出現的位置補上〔〕白話說明（若本卷前文已經說明過，就在 notes 註明出處），不要改動原詞。

**T9** · `review_terms` · `first_occurrence_format` · low

- Claim: 「同分」「彼同分」在問句中首次出現時沒有〔〕，白話說明延後到答句才附上，不符合首次出現格式。
- Required change: 把〔根與識共同作用於境〕〔根未與識共同作用〕移到問句中首次出現的「同分」「彼同分」之後，答句不再重複加註。

**T10** · `review_terms` · `first_occurrence_format` · low

- Claim: 「空、無願、無相」在問句中首次出現時沒有〔〕，白話說明延後到答句才附上，不符合首次出現格式。
- Required change: 把〔三種觀行：觀空、不願求三界諸有、離諸相〕移到問句首次出現的空、無願、無相處（可附在「無相」之後），答句不再重複加註。

### T1579-072-007: 07 五事修行應用與卷末

**T11** · `review_terms` · `first_occurrence_format` · med

- Claim: 「初靜慮〔靜慮定〕」的〔〕只是把原詞加上「定」字，沒有給一般讀者真正的白話解釋，等於只重複原詞，不符合首次出現格式。
- Required change: 首次出現的〔〕改成實際說明「靜慮」含義的白話，不能只重複「靜慮」原詞；如果因此偏離 glossary plain，要在 notes 說明。

**T12** · `review_terms` · `plain_wording` · low

- Claim: 「神通〔超越常人的能力〕」偏離 glossary abhijna 的 plain「五種神通」，notes 卻沒有說明偏離理由。
- Required change: 在 notes 說明此處「神通」是泛稱而不是「五神通」類名，所以不用 plain「五種神通」；或依 glossary plain 對齊〔〕用字。

**T13** · `review_terms` · `term_rendering` · low

- Claim: 〔〕內寫入「味境」，把原文沒有的術語帶進譯文；而「味境」正是 taste-object 的 translation_pattern，依 pattern 自動比對時會誤判成 taste-object 的 occurrence。這種較長的辨析也應放在 notes。
- Required change: 〔〕只保留「沒有差別的同一體性」等白話；「不是六境中的味境」的辨析移到 notes（notes 已有對應說明），不要讓譯文正文出現「味境」。

## How to close them

1. Edit `translations/T1579-072-baihua.md` by hand, or run a new audit job on the published text:
   `python3 scripts/audited.py audit --juans 72 --model opus --fix-rounds 2`. The audit starts from the
   published text, so its findings will differ from this list.
2. Rebuild the page and the search indexes the way `publish_unsealed.py` does, or let a passing audit do it.
3. A passing audit seals the juan. Until then juan 72 has no attestation.
