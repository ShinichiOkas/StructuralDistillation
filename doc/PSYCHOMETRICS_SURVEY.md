# 問いの立て方の調査 —— 心理測定・調査法の確立した手法と、本設計との対応

- 版: v1（2026-09-22）
- 出自: 測定 2 周目スプリント（`.pair-agent/agreements/measurement-2.md` M9）
- ⚠ 調査が教えるのは「空白の形」であって「作るもの」ではない。ここに書いた手法を設計に持ち込むかは、対応表の「取り入れる候補」ごとに**測って決める**
- ⚠ 文献は 2026-09-22 に Web で裏を取ったものだけを出典に挙げる。裏を取っていない記憶は「（未確認）」と書く

## 0. 師匠の言葉（原文・2026-09-22）

> 少し脱線するが、命題に対する問いの立て方について、現実の人間向けでも一つの事柄に複数視点から問を立てて意図的なバイアスの混入が難しいようにできているし、逆にランダムに答えたものも検出できるように設計されている。あの設計手法に確立されたものやり論、モデルが存在するのではないか？あるなら取り入れたほうが良いように思うので調査してほしい。

## 1. 一行で

> **ある。心理測定（psychometrics）と調査法（survey methodology）に、①項目対による不整合の検出 ②バランス尺度 ③不注意・無作為回答の検出指標 ④分散成分による信頼性（一般化可能性理論）⑤項目応答理論と person fit ⑥多特性多方法行列 ⑦テスト・ブループリントと項目作成規則、が確立している。本設計はこのうち ①②④ の一部を自前で再発明しており、③⑦ が欠けている。**

## 2. 確立された手法

### A. 項目対による不整合の検出 —— MMPI の VRIN / TRIN

- **何か**: MMPI-2 / MMPI-2-RF の妥当性尺度。**VRIN**（Variable Response Inconsistency）は内容が「似ている」または「反対の」項目対（MMPI-2 で 49 対、RF で 53 項目）を持ち、対の答えが食い違う数で**無作為回答**を検出する。**TRIN**（True Response Inconsistency）は反対の内容の対に**両方 True**（または両方 False）と答えた数で、**「はい」偏り（acquiescence）／「いいえ」偏り**を検出する。両者は別の尺度で、別の壊れ方を測る
- **何のためか**: 回答者が読まずに答えた・全部「はい」と答えた、を**内容の尺度とは独立に**見つける。閾値（T 70〜80）を超えたプロファイルは無効と扱う
- **文献**: Pearson の解説 [Interpretation of MMPI-2 Validity Scales](https://www.pearsonassessments.com/content/dam/school/global/clinical/us/assets/mmpi-2/interpretation-of-mmpi-2-validity-scales.pdf)／[MMPI-2-RF VRIN-r・TRIN-r の機能（無作為・acquiescence・counter-acquiescence の度合いを変えた実験）](https://www.researchgate.net/publication/41967807_Psychometric_Functioning_of_the_MMPI-2-RF_VRIN-r_and_TRIN-r_Scales_With_Varying_Degrees_of_Randomness_Acquiescence_and_Counter-Acquiescence)／[法廷場面での評価](https://pubmed.ncbi.nlm.nih.gov/27044444/)
- **本設計での対応物**: 軸の「支持側／反証側の排他な対」に両側とも証拠が出たときの**矛盾（u₄）**は TRIN と同じ量（反対の内容に両方「述べている」）。全問 Yes の偽読み手が矛盾率 1.0 になるのは TRIN が満点になるのと同型
- **欠けているもの**: **VRIN 的な「似た内容の対」が無い。** 同じ事実の言い換え 2 記述に違う答えを返す率（＝無作為・不注意）を測っていない。現在の矛盾率は TRIN 的な量だけで、「はい偏り」と「無作為」を分けられない

### B. バランス尺度と逆キー項目

- **何か**: 同じ構成概念を、順キー（同意が高得点）と逆キー（同意が低得点）の項目で**同数**測る。「はい」偏りの回答者は両方に同意するので、合計すると偏りが**相殺**する。Weijters & Baumgartner は順・逆を同数含む**item parcel**（項目束）を作ると method 効果が打ち消えると示した
- **注意点（同じ著者の総説）**: 逆キー項目は**誤反応（misresponse）**を招く。とくに「〜ない」という**否定語**で作った逆キー項目は認知負荷が高く、反対の内容を**肯定文**で書いた項目（polar opposite）より壊れやすい
- **文献**: [Weijters & Baumgartner (2012) Misresponse to Reversed and Negated Items in Surveys: A Review](https://journals.sagepub.com/doi/10.1509/jmr.11.0368)／[Weijters & Baumgartner (2022) Balanced Item Parceling](https://journals.sagepub.com/doi/abs/10.1177/1094428121991909)／[Reversed Item Bias: An Integrative Model](https://www.researchgate.net/publication/236640822_Reversed_Item_Bias_An_Integrative_Model)／[逆キー項目の心理測定（2025・言語的視点）](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1684612/full)
- **本設計での対応物**: v2 の「支持観点の記述 4 ＋ 反証観点の記述 4」は**バランス尺度そのもの**だった。空撃ちで崩れた理由（本文を読める生成器が両側とも真の事実で埋める）は、人間向けの尺度では起きない**LLM 固有の壊れ方**である —— 人間の回答者は項目を書かないが、本設計では生成器が項目を書く。p3 の「軸ごとの排他な対」は、Weijters の **balanced parcel**（順・逆を同じ束にする）に近い
- **否定語の知見は本設計の実測と一致する**: 空撃ち 1 周目で「否定形の疑問文」が矛盾率 0.60 を作り、平叙文にして 0.00 になった（D1）。Weijters の「否定語より反対内容の肯定文」は H11 の精緻化に当たる

### C. 不注意・無作為・無努力回答（careless / insufficient effort responding）の検出

- **何か**: 調査データで「読まずに答えた」回答者を見つける指標群。**Meade & Craig (2012)** と **Curran (2016)** が体系化した。主なもの:
  - **指示付き項目（instructed response items）**: 「この項目は『反対』を選んでください」。読んでいれば必ず特定の答えになる
  - **虚偽項目（bogus items）**: 「私は妖精から隔週で給料をもらっている」。読んでいれば必ず否定する
  - **長い連続（longstring）**: 同じ答えが何項目続くか
  - **偶奇一致（even-odd consistency）**: 同じ尺度の偶数項目と奇数項目の合計の相関
  - **心理測定的同義語・反義語（psychometric synonyms / antonyms）**: 強く正相関（負相関）するはずの項目対の、その回答者内での相関
  - **多変量外れ値（Mahalanobis 距離）**、**回答時間**
- **文献**: [Curran (2016) Methods for the detection of carelessly invalid responses](https://www.sciencedirect.com/science/article/abs/pii/S0022103115000931)／[Annual Review of Psychology (2024) Dealing with Careless Responding: Prevention, Identification, and Recommended Best Practices](https://www.annualreviews.org/content/journals/10.1146/annurev-psych-040422-045007)／R パッケージ [careless](https://cran.r-project.org/web//packages/careless/careless.pdf)（longstring・偶奇一致・同義語/反義語・IRV・Mahalanobis を実装）／[不注意の開始点の検出（2023）](https://arxiv.org/html/2303.07167v3)
- **本設計での対応物**: **偽読み手**（全問 Yes・全問 触れていない）は較正用の既知不良で、本番の判定には混ざらない。矛盾率は反義語対の一種。**longstring 相当**（全軸同じ答え）は偽読み手でしか見ていない
- **欠けているもの**: **本番の軸集合に検出項目を混ぜる**という定番がない。人間向け調査では、指示付き項目・虚偽項目を数個混ぜて**その回答者のその回答**を無効にする。本設計では「読み手が今回の本文を読んだか」を毎回測る手段が無い（S10 の反事実は実験でしか使えない）

### D. 一般化可能性理論（G 理論）

- **何か**: Cronbach らが 1972 年に体系化した信頼性理論。単一の真値・単一の誤差の代わりに、**測定対象（universe score）と、項目・採点者・機会などの facet ごとの分散成分**を ANOVA で推定する（G-study）。そのうえで「採点者を何人・項目を何個にすれば信頼性がいくつになるか」を予測する（**D-study**）。相対判断の G 係数と絶対判断の Φ（dependability）係数を区別する
- **文献**: [Shavelson, Webb & Rowley — Generalizability Theory](https://www.researchgate.net/profile/Richard-Shavelson/publication/232586408_Generalizability_Theory/links/00b7d537636b397f27000000/Generalizability-Theory.pdf)／[Brennan — NCME Module 14](https://ncme.org/wp-content/uploads/2025/10/Module-14-Generalizability-Theory-Brennan-Winter-1.pdf)／[AI 時代の G 理論再考（2025）](https://www.sciencedirect.com/science/article/pii/S2666557325000370)／LLM 評価で同型: [Messing (2026) Hidden Measurement Error in LLM Pipelines](https://arxiv.org/pdf/2604.11581) —— 判定モデル・温度・プロンプト表現の分散を分け、素朴な標準誤差が 40〜60% 小さすぎることを示し、小規模パイロットで分散成分を推定してから設計を決めることを勧める
- **本設計での対応物**: 読み手間の Δ（採点者 facet）・標本間の |p 差|（機会 facet）・軸数不変性 S12（項目 facet）は、**G 理論の facet を 1 つずつ手で測っている**形。確からしさを「診断値の組」で返すのは、分散成分を並べるのと同じ発想。ただし**交互作用**（読み手 × 軸 など）は測っておらず、**D-study（あと何体・何本で幅が縮むか）**も無い
- **欠けているもの**: 分散成分の推定と D-study。「読み手を 2 体から 3 体にすると確からしさがどれだけ上がるか」を返せれば、費用の可視性（Q8）と直結する

### E. 項目応答理論（IRT）・Rasch と person fit

- **何か**: 回答を「回答者の潜在特性 θ」と「項目の難易度・識別力」で説明するモデル。**person fit 統計**（lz・infit・outfit）は、ある回答者の回答パターンが**モデルから外れている**度合い（無作為・当て推量・回答セット）を検出する。項目側の fit は「モデルで説明できない項目」を見つける
- **文献**: [Lz — An All-Purpose Person Fit Statistic?（Rasch Measurement Transactions）](https://www.rasch.org/rmt/rmt113n.htm)／[lz* の解説](https://metricgate.com/docs/lz-star-person-fit-snijders/)／LLM 評価への適用: [Lost in Benchmarks? IRT で LLM ベンチマークを再考（2025）](https://arxiv.org/abs/2505.15055)／[Auditing LLM Benchmarks with IRT（2026）](https://arxiv.org/pdf/2605.30504)／[Adaptive Testing for LLM Evaluation（2025）](https://arxiv.org/pdf/2511.04689)／総説 [Ye ほか (2025) Large Language Model Psychometrics: A Systematic Review](https://arxiv.org/pdf/2505.08245)
- **本設計での対応物**: 読み手 ＝ 回答者、軸 ＝ 項目、と置けば **読み手の person fit** が「計器不良」の検出に、**軸の識別力**（題材をまたいで向きが変わるか）が「情報の無い軸」の検出に当たる。空撃ちの「分布が潰れていないか」の検査（0.5 の張り付き）は項目分析の素朴版
- **欠けているもの**: 母数。IRT は項目 × 回答者の行列が要る。本設計は 1 判定あたり軸 6 × 読み手 2 で、モデルを当てるには小さい。**多くの判定を溜めた後**の話

### F. 多特性多方法行列（MTMM）

- **何か**: Campbell & Fiske (1959)。複数の特性（trait）を複数の方法（method）で測り、同じ特性を違う方法で測った値が一致すること（**収束的妥当性**）と、違う特性が違う値になること（**弁別的妥当性**）を同時に見る。方法に由来する分散（**method variance**）を切り出す
- **文献**: [Campbell & Fiske (1959)](https://www.semanticscholar.org/paper/Convergent-and-discriminant-validation-by-the-Campbell-Fiske/7752e0835506a6629c1b06e67f2afb1e5d2bb714)／[解説（Research Methods Knowledge Base）](https://conjointly.com/kb/multitrait-multimethod-matrix/)
- **本設計での対応物**: 命題 ＝ trait、読み手 ＝ method。**読み手非依存（Q1・S1）は収束的妥当性**そのもの。弁別的妥当性（同じ本文に違う命題を当てたら違う値になる）は「想定一致」で暗黙に見ているだけ
- **欠けているもの**: 弁別の側の明示的な検査（同じ本文・複数の命題で、値が命題ごとに違うか）。読み手の癖（method variance）を数として切り出すこと

### G. テスト・ブループリント（table of specifications）と項目作成規則

- **何か**: 測りたい内容領域 × 認知レベルの表を先に作り、各セルに何項目置くかを決めてから項目を書く（内容的妥当性の証拠になる）。項目作成には経験的な規則集がある（Haladyna, Downing & Rodriguez 2002 の 31 項目: 1 項目 1 内容、否定語を避ける、あいまいな語を避ける、正解の手がかりを入れない、など）。書かれた項目は**項目レビュー**（複数の専門家が内容・偏りを審査）を通す
- **文献**: [Haladyna, Downing & Rodriguez (2002) A Review of Multiple-Choice Item-Writing Guidelines](https://cmapspublic3.ihmc.us/rid=1P2XTLCSS-11K09T9-BD5/Haladyna_2002_-Appl_Meas_Educ.pdf)／[Haladyna & Rodriguez — Developing and Validating Test Items](https://www.routledge.com/Developing-and-Validating-Test-Items/Haladyna-Rodriguez/p/book/9780415876056)／[Test Specifications and Blueprints: Reality and Expectations](https://www.researchgate.net/publication/322232987_Test_Specifications_and_Blueprints_Reality_and_Expectations)
- **本設計での対応物**: ハーネス H2（観察可能）・H3（非自明）・H4（非重複）・H11（平叙文）は項目作成規則の一部と重なる。**H12（向きの交差検証）は項目レビューに当たる**（別の審査者が向きを検める）
- **欠けているもの**: **ブループリント**。生成器は「命題の真偽を分ける事柄を 6 つ」と言われるだけで、何を網羅すべきかの表を持たない。評価的な命題で向きの誤りが出た（m01「悪人である」で「船員を救助した」が支持側）のは、「行為／動機／結果／第三者の評価／反対証拠」のような**セルの指定**が無いことにも由来しうる

### H. 参考（対応は薄い）

- **強制選択・ipsative 尺度**（faking 対策）: 本設計の「対のどちらを本文が述べているか」は形として近い。人間向けは望ましさを揃えた対を使う
- **信頼性係数**: Cronbach α（項目間）、Cohen κ・Krippendorff α・ICC（採点者間）。読み手間の一致を Δ ではなく標準の係数で出す手はある（母数が要る）
- **認知面接・プリテスト**: 項目の文言を回答者に声に出して解釈させる。LLM に「この記述をどう読んだか」を言わせる腕に相当（未検証）

## 3. 対応表

| 本設計の要素 | 当たる手法 | 状態 |
|---|---|---|
| 軸の排他な対（支持側／反証側） | B バランス尺度・balanced parcel | あり。人間向けと違い**項目を LLM が書く**ので、同数制約だけでは崩れた（空撃ち D3） |
| 矛盾 u₄（両側に証拠） | A TRIN・C 反義語対 | あり。「はい偏り」の計器。無作為とは分けられない |
| 同じ事実の言い換えの対 | A VRIN・C 同義語対 | **無い** |
| 偽読み手（全問 Yes・全問 触れていない） | C longstring・A TRIN の較正 | あり。較正用のみ。本番には混ざらない |
| 指示付き項目・虚偽項目 | C | **無い** |
| 読み手間の Δ | D 採点者 facet・F 収束的妥当性・H κ/ICC | あり（素朴な差） |
| 標本間の \|p 差\| | D 機会 facet | あり |
| 軸数不変性 S12 | D 項目 facet | あり（要求だけ） |
| 交互作用・D-study | D | **無い** |
| 反事実 S10 | （人間向けには無い。LLM 固有） | あり。実験でしか使えない |
| ハーネス H2・H3・H4・H11 | G 項目作成規則 | あり（一部） |
| H12 向きの交差検証 | G 項目レビュー | 測定中 |
| ブループリント | G | **無い** |
| 軸の識別力・読み手の person fit | E | 無い（母数不足） |
| 弁別的妥当性 | F | 暗黙（想定一致） |

## 4. 取り入れる候補（優先順。どれも「測って決める」）

| 順 | 候補 | 何が変わるか | 費用 | 採否を決める測定 |
|---|---|---|---|---|
| 1 | **検出項目を本番の軸集合に混ぜる**（C）: 本文に明らかに述べられている事実の記述（必ず「述べている」）と、本文に絶対無い記述（必ず「触れていない」）を各 1 軸。外れたらその読み手のその判定を「計器不良」 | 本番で毎回、読み手が本文を読んだかを測れる。S10 は実験用、こちらは運用用 | 軸 2 本分の呼び出し増 | 偽読み手で必ず検出（全問 Yes は「絶対無い記述」で落ちる）、健全な読み手で偽陽性 0 |
| 2 | **同義対を足して矛盾率を分ける**（A VRIN）: 各軸の支持側の記述に**言い換え**を 1 本足し、答えの食い違い率を「無作為・不注意」の計器、両側証拠を「はい偏り」の計器として別々に返す | 矛盾の出所（読み手の偏り／対の非排他性／読み手の不注意）を分けられる | 軸あたり記述 1 本増 | 弱い読み手（p3 記録）で言い換えの食い違い率が偏り率より高く出るか。強い読み手で両方 0 に近いか |
| 3 | **ブループリントを生成器の指示に**（G）: 命題の型ごとに軸が網羅すべきセル（評価的なら 行為／動機や言い分／結果／第三者の評価／反対証拠）を表で渡す | 向きの誤り・軸の偏り（同じ種類の事実ばかり）が減る見込み | 指示の版が増える | 向きの誤り率（M1）と想定一致が上がるか。セルを渡さない版と同じ題材で比べる |
| 4 | **分散成分と D-study**（D）: 集約 L3 で 軸 × 読み手 × 標本 の分散成分を推定し、確からしさの診断値に G 係数・Φ 係数を足す。「読み手を 1 体足すと幅がどれだけ縮むか」を返す | 確からしさが「数えられる量の組」から**構造のある量**になる。費用の見積もりが出せる | コードだけ | 空撃ちの記録（21 題材 × 2〜4 読み手 × 2〜4 標本）で分散成分が推定でき、標本 4 の腕の値を予測できるか |
| 5 | **逆キーは否定語でなく反対内容の肯定文**（B）: H11 に明文化 | 既に p3 の claim_refute はほぼこの形。規則として固定する | なし | — |
| 6 | 軸の識別力・読み手の person fit（E） | 情報の無い軸・壊れた読み手を統計で落とせる | 記録が溜まってから | 判定 100 件以上 |

## 5. 空白の形 —— 人間向けの手法が答えていないこと

- **項目を書くのが回答者側の LLM である**こと。人間向けの尺度は専門家が項目を書き、回答者は答えるだけ。本設計は生成器（LLM）が項目を書き、読み手（LLM）が答える。バランス尺度が空撃ちで崩れたのはこの差から出た（同数制約を「真の事実で埋める」）。**項目レビュー（H12）とブループリント（候補 3）は、この差を埋める人間側の手続き**に当たる
- **本文が変えられる**こと。人間向けの妥当性検証は回答者の内面を変えられないが、本設計は本文を書き換えられる（反事実 S10）。これは心理測定に無い計器で、残す
- **同じ「回答者」を何度でも呼べる**こと。標本 facet が安く取れる。G 理論の D-study が本設計では**実行時に**回せる

## 6. 出典

- [Interpretation of MMPI-2 Validity Scales (Pearson)](https://www.pearsonassessments.com/content/dam/school/global/clinical/us/assets/mmpi-2/interpretation-of-mmpi-2-validity-scales.pdf)
- [Psychometric Functioning of the MMPI-2-RF VRIN-r and TRIN-r Scales](https://www.researchgate.net/publication/41967807_Psychometric_Functioning_of_the_MMPI-2-RF_VRIN-r_and_TRIN-r_Scales_With_Varying_Degrees_of_Randomness_Acquiescence_and_Counter-Acquiescence)
- [Inconsistent Responding in a Criminal Forensic Setting: VRIN-r and TRIN-r](https://pubmed.ncbi.nlm.nih.gov/27044444/)
- [Weijters & Baumgartner (2012) Misresponse to Reversed and Negated Items in Surveys: A Review](https://journals.sagepub.com/doi/10.1509/jmr.11.0368)
- [Weijters & Baumgartner (2022) On the Use of Balanced Item Parceling to Counter Acquiescence Bias](https://journals.sagepub.com/doi/abs/10.1177/1094428121991909)
- [Reversed Item Bias: An Integrative Model](https://www.researchgate.net/publication/236640822_Reversed_Item_Bias_An_Integrative_Model)
- [Advancing the psychometrics of reverse-keyed items (Frontiers in Psychology, 2025)](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1684612/full)
- [Curran (2016) Methods for the detection of carelessly invalid responses in survey data](https://www.sciencedirect.com/science/article/abs/pii/S0022103115000931)
- [Dealing with Careless Responding in Survey Data (Annual Review of Psychology, 2024)](https://www.annualreviews.org/content/journals/10.1146/annurev-psych-040422-045007)
- [R package careless](https://cran.r-project.org/web//packages/careless/careless.pdf)
- [Shavelson, Webb & Rowley — Generalizability Theory](https://www.researchgate.net/profile/Richard-Shavelson/publication/232586408_Generalizability_Theory/links/00b7d537636b397f27000000/Generalizability-Theory.pdf)
- [Brennan — Generalizability Theory (NCME Module 14)](https://ncme.org/wp-content/uploads/2025/10/Module-14-Generalizability-Theory-Brennan-Winter-1.pdf)
- [Messing (2026) Hidden Measurement Error in LLM Pipelines Distorts Annotation, Evaluation, and Benchmarking](https://arxiv.org/pdf/2604.11581)
- [Lz — An All-Purpose Person Fit Statistic? (Rasch Measurement Transactions)](https://www.rasch.org/rmt/rmt113n.htm)
- [Lost in Benchmarks? Rethinking LLM Benchmarking with Item Response Theory (2025)](https://arxiv.org/abs/2505.15055)
- [Auditing LLM Benchmarks with Item Response Theory (2026)](https://arxiv.org/pdf/2605.30504)
- [Adaptive Testing for LLM Evaluation (2025)](https://arxiv.org/pdf/2511.04689)
- [Ye et al. (2025) Large Language Model Psychometrics: A Systematic Review](https://arxiv.org/pdf/2505.08245)
- [Campbell & Fiske (1959) Convergent and discriminant validation by the multitrait-multimethod matrix](https://www.semanticscholar.org/paper/Convergent-and-discriminant-validation-by-the-Campbell-Fiske/7752e0835506a6629c1b06e67f2afb1e5d2bb714)
- [Multitrait-Multimethod Matrix (Research Methods Knowledge Base)](https://conjointly.com/kb/multitrait-multimethod-matrix/)
- [Haladyna, Downing & Rodriguez (2002) A Review of Multiple-Choice Item-Writing Guidelines](https://cmapspublic3.ihmc.us/rid=1P2XTLCSS-11K09T9-BD5/Haladyna_2002_-Appl_Meas_Educ.pdf)
- [Haladyna & Rodriguez — Developing and Validating Test Items](https://www.routledge.com/Developing-and-Validating-Test-Items/Haladyna-Rodriguez/p/book/9780415876056)
- [Test Specifications and Blueprints: Reality and Expectations](https://www.researchgate.net/publication/322232987_Test_Specifications_and_Blueprints_Reality_and_Expectations)
