SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

# SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

Oct 6, 2026 · @Eya 

The most defensible Track 1 project is a **hybrid, physics-aware detector that fuses IQ/spectrum-level evidence with frame/protocol-level evidence and explicitly separates benign channel degradation from attacks** . The literature supports each building block individually, but no paper we found evaluates all of them together on a satellite ground link with false-positive rate, latency and adversarial robustness reported side by side. That integration, measured properly, is the contribution. 

**How to read this review.** Every recent paper below was checked against a publisher page, DOI, arXiv record or the authors' institutional page during this review; a few seminal classics are cited from standard bibliographic records and are listed as such under Sources. Tags used throughout: 

- **[PR]** peer-reviewed (journal or conference) · **[WS]** peer-reviewed workshop · **[PP]** preprint only (arXiv) · **[DS]** dataset record 

- **[SAT]** satellite- or space-specific research · **[GNSS]** satellite navigation (transferable, but a different link) · **[GEN]** generic RF/wireless or generic ML 

**Code** = public code or model weights exist; **Data** = the paper's dataset is public 

No search found a public description of the TSYP14 / GRSS "Ground Truth Under Attack" Track 1 dataset, so its format is treated as unknown. Section 5 explains how to complement it whatever its format turns out to be. Numbers quoted from papers are the authors' own; none were reproduced here. 

## 1. Research problem 

The task is a **three-way decision under distribution shift** , not a binary classifier: for each observation window, decide whether the ground link is nominal, benignly degraded, or under attack, and if attacked, by what and with what confidence, within a latency budget and at a controlled false-alarm rate. 

### 1.1 Formal statement 

Let x_t be what the ground station observes in window t: complex baseband samples, derived spectral/statistical features, and decoded frame fields (sequence counters, timestamps, CRC/FEC status, MAC/authentication result where present). Define three 

Page 1 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### hypotheses: 

```
\mathcal{H}_0:\ x_t \sim p_{\text{nominal}}(x \mid g_t) \qquad
\mathcal{H}_D:\ x_t \sim p_{\text{degraded}}(x \mid g_t, w_t) \qquad
\mathcal{H}_A^{(k)}:\ x_t \sim p_{\text{attack}}^{(k)}(x \mid g_t),\ k \in \
{\text{jam, spoof, replay, inject, seq, timing}\}
```

Here g_t is the **known pass geometry** (elevation, range, Doppler, all predictable from the satellite ephemeris) and w_t is unobserved weather, multipath and receiver state. The detector outputs a score s(x_t), a decision, a class k, and an explanation. The operating constraint is a bounded false-alarm rate P(alarm | H_0 ∪ H_D) ≤ α at a stated latency, not maximum accuracy. This framing follows the security-ML literature, which shows that accuracy on balanced test sets hides the base-rate problem (Axelsson, ACM TISSEC <u>2000; Sommer & Paxson, IEEE S&P 2010; Arp et al., USENIX Security 2022).</u> 

### 1.2 Where attacks occur and what can be observed 

|Level|Attack examples|Observables at the<br>ground station|Representative<br>evidence|
|---|---|---|---|
|Physical /<br>signal|Barrage, tone, chirp,<br>pulsed, intermittent<br>jamming;<br>overshadowing|Received power, noise<br>floor, PSD shape,<br>spectral kurtosis, in-<br>band vs out-of-band<br>energy, AGC level|Xu et al. 2005;<br>Gecgel &<br>Kurt 2021|
|Waveform /<br>IQ|Spoofed transmitter,<br>replayed recording,<br>adversarial<br>perturbation|Transmitter impairments<br>(CFO, IQ imbalance,<br>phase noise, PA<br>nonlinearity),<br>constellation<br>spread/EVM,<br>correlation-peak<br>distortion, fading<br>statistics|Smailes et al. CCS<br>2023;<br>Oligeri et al. TIFS<br>2023|
|Frame / link|Frame injection, replay,<br>selective jamming of<br>headers|Sync-word detection<br>rate, CRC/FEC failure<br>pattern, frame length,<br>inter-arrival time|Salkield et al. WiSec<br>2023|



Page 2 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Level|Attack examples|Observables at the<br>ground station|Representative<br>evidence|
|---|---|---|---|
|Protocol|Sequence-number<br>manipulation,<br>duplicate/out-of-<br>window counters,<br>COP-1 abuse,<br>command flooding|Counter<br>gaps/regressions,<br>retransmission requests,<br>authentication failures,<br>command grammar|CCSDS 355.0-B SDLS;<br>COP-1 misbehaviour<br>detection, JEIT|
|Data /<br>telemetry|Manipulated telemetry<br>values, false data<br>injection, stale<br>(replayed) telemetry|Physically inconsistent<br>values, broken cross-<br>channel correlation,<br>timestamp vs content<br>mismatch|Hundman et al. KDD<br>2018;<br>ESA-ADB|



### 1.3 Why detection is hard on a satellite ground link 

1. **The benign channel is non-stationary.** LEO passes sweep elevation, path loss and Doppler within minutes; rain fade and multipath change the noise floor. PAST-AI notes high attenuation, multipath fading, strong Doppler and short link duration as the core difficulty for LEO fingerprinting (Oligeri et al.). 

2. **Attacks look like degradation.** A low-power barrage jammer is indistinguishable from a raised noise floor in a single power reading. Xu et al. showed that no single statistic (signal strength, carrier-sense time, or packet delivery ratio) separates jamming from poor links, and proposed consistency checks across them (MobiHoc 2005). 

3. **Smart attackers stay inside the noise.** Intermittent jamming that targets specific telemetry fields was the hardest case for both CNN and SVM detectors in Gecgel & <u>Kurt; overshadowing spoofers can take over AGC without obvious power anomalies</u> (Salkield et al.). 

4. **Replays are physically perfect at the content level.** A replayed frame passes CRC and, without SDLS, passes every protocol check except freshness. Only freshness metadata or the replaying hardware's fingerprint betrays it (SatIQ). 

5. **Labels are scarce and attacks are rare.** Most realistic data is normal-only, which pushes toward one-class / reconstruction methods; but those are the methods most likely to flag benign novelty (a new pass geometry, a new weather condition) as an attack. 

6. **The detector itself is attackable.** DL signal classifiers can be evaded with perturbations far weaker than jamming (Sadeghi & Larsson). 

Page 3 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

### 1.4 Degradation versus attack: the separating principle 

Natural degradation is **physically self-consistent** ; attacks usually break a physical or protocol invariant somewhere. Useful invariants for a ground link: 

- **Power–quality consistency:** low SNR should produce proportionally more frame errors. High received power with a collapsing delivery ratio indicates jamming (Xu et <u>al.).</u> 

- **Geometry consistency:** expected path loss and Doppler follow the pass. A sudden power step, or a Doppler curve that does not match the ephemeris, suggests a terrestrial transmitter. This is an inference from orbital mechanics; we found no paper that evaluates it as an ML feature for ground-link attack detection (see Section 7). 

- **Distortion consistency:** multipath distorts the correlation peak differently from a spoofer's aligned signal. The power-and-distortion (PD) monitor was evaluated on spoofing, multipath-dense and jamming recordings precisely to separate these (Wesson et al.). 

- **Hardware consistency:** the legitimate transmitter's impairments persist across fades; a replay or spoof carries a different device's impairments (SatIQ; <u>FadePrint).</u> 

- **Protocol consistency:** benign errors cause random CRC failures and retransmissions; attacks cause structured patterns (duplicate counters, counters jumping backward, valid CRC with stale timestamp). The COP-1 misbehaviour paper models both an attacker and the fading channel to separate them with a hypothesis test (JEIT). 

### 1.5 The main false-positive sources 

|Source|Why it fires|Mitigation supported by<br>literature|
|---|---|---|
|Low-elevation segments of<br>a pass|SNR drop and multipath<br>resemble jamming|Condition the detector on<br>elevation/SNR; per-geometry<br>thresholds|
|Rain fade / weather|Slow noise-floor rise|Temporal smoothing; require<br>power–quality inconsistency|
|Receiver restarts, AGC<br>transients, gain changes|Step changes in<br>power/PSD|Rule-based suppression with<br>logged receiver state|
|Unseen but benign signal<br>types|One-class models flag any<br>novelty|Semi-supervised feedback, as<br>in crowdsourced SAIFE follow-<br>up (<br>Rajendran et al.)|



Page 4 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Source|Why it fires|Mitigation supported by<br>literature|
|---|---|---|
|Point-wise thresholds on<br>noisy scores|Isolated spikes|Dynamic, nonparametric<br>thresholding with pruning of<br>anomaly sequences; pruning<br>raised precision from 48.9% to<br>87.5% for a 4.8-point recall loss<br>(<br>Hundman et al.)|
|Dataset shift between<br>recordings|Classifier learned the<br>recording, not the attack|Cross-recording splits;<br>Fraunhofer showed accuracy<br>drops when combining GNSS<br>interference datasets (<br>Raichur<br>et al.)|



## 2–3. State of the art, organised by category 

The strongest satellite-specific security results come from one group (Oxford/armasuisse: SatIQ, Sticky Fingers, Satellite Spoofing A–Z) and one Italian–Dutch– Qatari group (PAST-AI, FadePrint), both working on Iridium IQ data with public datasets. Most ML jamming and spoofing work is GNSS-specific; most adversarial-ML work targets modulation classifiers, not anomaly detectors. Each entry compresses the 16 requested fields into eight rows. 

### Category A — AI for RF / signal anomaly detection 

#### **A1. Recurrent Neural Radio Anomaly Detection** 

|Field|Detail|
|---|---|
|Bibliographic|T. J. O'Shea, T. C. Clancy, R. W. McGwier · 2016 ·<br>arXiv ·<br>arXiv:1611.00301 · [PP][GEN]|
|Problem / attack model|Novelty detection on raw radio time series;<br>anomalies are generic (not adversarial)|
|Data / input|Synthetic radio scenarios; raw time-domain<br>samples|
|Features / model|LSTM predictor; anomaly score = prediction error|
|Method / metrics|Train on normal, flag large prediction residuals;<br>small-scale evaluation|



Page 5 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Results|Proof of concept that prediction-error detection<br>works directly on the radio physical layer(<br>ar5iv)|
|Limitations|Preprint; no FPR/latency study; no public<br>benchmark|
|Relevance|Seminal origin of predictive-residual RF anomaly<br>detection; cite as the baseline idea, do not<br>reimplement as is|



#### **A2. Deep Predictive Coding Neural Network for RF Anomaly Detection in Wireless Networks** 

|Field|Detail|
|---|---|
|Bibliographic|N. Tandiya, A. Jauhar, V. Marojevic, J. H. Reed · 2018<br>· IEEE ICC Workshops ·<br>arXiv:1803.06054 · [PR]<br>[GEN]|
|Problem / attack model|Unsupervised RF anomaly detection; anomalies<br>include jamming, chirping transmitters, spectrum<br>hijacking, node failure|
|Data / input|Authors' generated dataset; image sequences from<br>spectrograms and spectral correlation functions|
|Features / model|PredNet video-prediction network (convolutional<br>LSTM) on time-frequency image sequences|
|Method / metrics|Train on normal; alarm on actual-vs-predicted<br>deviation; detection ratio and false-alarm rate<br>(<br>arXiv)|
|Results|Reported better than O'Shea et al.'s LSTM<br>approach(<br>survey summary)|
|Limitations|Heavy model (video prediction); terrestrial;<br>dataset not standard|
|Relevance|Shows that**spectral correlation (cyclostationary)**<br>**images**add value beyond spectrograms; reports<br>FAR, which most RF papers omit|



Page 6 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **A3. SAIFE: Unsupervised Wireless Spectrum Anomaly Detection with Interpretable Features** 

|Field|Detail|
|---|---|
|Bibliographic|S. Rajendran, W. Meert, V. Lenders, S. Pollin ·<br>DySPAN 2018; journal version IEEE TCCN vol. 5 no.<br>3, 2019, pp. 637–647 ·<br>IEEE ·<br>arXiv:1807.08316 · [PR]<br>[GEN]|
|Problem / attack model|Spectrum anomalies: unwanted signal in a band, or<br>absence of an expected signal|
|Data / input|Power spectral density (PSD) vectors; three<br>datasets plus 500 h from an Electrosense sensor|
|Features / model|Adversarial autoencoder; latent space trained<br>semi-supervised to encode bandwidth, class and<br>centre frequency|
|Method / metrics|Reconstruction error for detection and localisation;<br>detection at constant false-alarm rate|
|Results|Average detection above 80% at a constant 1%<br>false-alarm rate, with localisation; semi-supervised<br>classification near 100% using 20% labels (<br>IEEE)|
|Limitations|PSD only (no IQ fine structure, so blind to<br>replay/spoofing); terrestrial|
|Relevance|Best template for an**explainable, CFAR-calibrated**<br>**unsupervised spectrum detector**; directly<br>reusable for the jamming/interference branch|



#### **A4. GenAI-Based Models for NGSO Satellites Interference Detection** 

|Field|Detail|
|---|---|
|Bibliographic|A. Saifaldawla, F. Ortiz, E. Lagunas, A. B. M. Adam, S.<br>Chatzinotas · 2024 · IEEE Trans. Machine Learning<br>in Communications and Networking ·<br>IEEE<br>10570488 ·<br>author PDF · [PR][SAT]. Precursor:<br>AE-<br>based NGSO-to-GSO detection, PIMRC 2023|
|Problem / attack model|Detect NGSO (LEO) interference in a GSO<br>downlink at the ground station; interference is<br>unintentional coexistence, not adversarial|



Page 7 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Data / input|Emulated scenarios using real satellite positions,<br>trajectories and system parameters; time- and<br>frequency-domain received signal|
|Features / model|Autoencoder, VAE, and a transformer-based<br>interference detector (TrID)|
|Method / metrics|Models generate the expected GSO signal;<br>reconstruction error flags interference; compared<br>with an energy detector|
|Results|TrID beat the other models and the energy<br>detector, by up to 31.23% in detection accuracy<br>(<br>IEEE)|
|Limitations|Simulated; non-adversarial; transformer cost not<br>justified for edge|
|Relevance|**Closest satellite-ground-station analogue**of a<br>learned "expected signal" model; supports using<br>the energy detector as the mandatory baseline|



#### **A5. Over-the-Air Deep Learning Based Radio Signal Classification (RadioML 2018.01A)** 

|Field|Detail|
|---|---|
|Bibliographic|T. J. O'Shea, T. Roy, T. C. Clancy · 2018 · IEEE J.<br>Selected Topics in Signal Processing ·<br>DOI<br>10.1109/JSTSP.2018.2797022 · [PR][GEN]|
|Problem / attack model|Modulation classification (no attacks)|
|Data / input|RadioML 2018.01A: about 2.5 M frames, 1024 IQ<br>samples each, 24 modulations, SNR −20 to +30 dB<br>(<br>dataset description)|
|Features / model|1-D CNN and ResNet on raw IQ|
|Method / metrics|Supervised classification accuracy vs SNR|
|Results|ResNet reaches about 95% at high SNR (<br>summary)|
|Limitations|Not a security task; synthetic with some over-the-<br>air data|



Page 8 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Relevance|The standard**IQ backbone and pretraining corpus**;<br>the adversarial-ML literature (Category F) attacks|
||exactly these models|



#### **A6. Large Scale Radio Frequency Signal Classification (Sig53 / TorchSig)** 

|Field|Detail|
|---|---|
|Bibliographic|L. Boegner, M. Gulati, G. Vanhoy, P. Vallance, B.<br>Comar, S. Kokalj-Filipovic, C. Lennon, R. D. Miller ·<br>2022 · Proc. GNU Radio Conference ·<br>GRCon ·<br>arXiv:2207.09918 · [PR][GEN] · Code|
|Problem / attack model|Narrowband signal classification with realistic<br>impairments|
|Data / input|Sig53: 5 M synthetic IQ samples, 53 classes;<br>generated on the fly|
|Features / model|EfficientNet and XCiT transformers on IQ|
|Method / metrics|Classification accuracy; ablations on domain-<br>specific augmentation|
|Results|Transformers beat ConvNets without extra<br>regularisation; RF-specific augmentation helps<br>(<br>arXiv)|
|Limitations|Synthetic; no attack classes|
|Relevance|**TorchSig is the practical tool to synthesise**<br>**impairments**(CFO, phase noise, fading) and to<br>build benign-degradation data for H_D|



Page 9 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

### Category B — Satellite / space communication security 

#### **B1. Watch This Space: Securing Satellite Communication through Resilient Transmitter Fingerprinting (SatIQ)** 

|Field|Detail|
|---|---|
|Bibliographic|J. Smailes, S. Köhler, S. Birnbach, M. Strohmeier, I.<br>Martinovic · 2023 · ACM CCS ·<br>DOI<br>10.1145/3576915.3623135 ·<br>arXiv:2305.06947 · [PR]<br>[SAT] · Code (<br>GitHub)· Data(<br>Zenodo)· Weights<br>(<br>Zenodo). Extended journal version: "SatIQ:<br>Extensible and Stable Satellite Authentication<br>using Hardware Fingerprinting",<br>DOI<br>10.1145/3768619|
|Problem / attack model|Authenticate legacy satellite downlinks without<br>cryptography; attacker spoofs or**replays**captured<br>messages with an SDR|
|Data / input|Iridium ring-alert message headers captured at 25<br>MS/s; the Zenodo record lists 1,706,556 messages<br>with satellite ID, position, timestamp and noise<br>estimate (<br>Zenodo)|
|Features / model|Raw high-rate IQ of headers; autoencoder encoder<br>+ Siamese network trained with triplet loss →<br>embedding distance|
|Method / metrics|One-shot verification against enrolled transmitter<br>embeddings; EER, ROC AUC|
|Results|Under SDR replay: EER 0.120, ROC AUC 0.946<br>(<br>abstract)|
|Limitations|Needs high sample rate; performance degrades<br>with atmospheric noise and multipath (studied in<br>the paper); Iridium-specific; attacker uses a single<br>SDR class|
|Relevance|**The single most relevant paper**: satellite-specific,<br>replay detection at the physical layer, public data,<br>code and weights, and a metric (EER) that exposes<br>the false-positive trade-off|



Page 10 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **B2. PAST-AI: Physical-Layer Authentication of Satellite Transmitters via Deep Learning** 

|Field|Detail|
|---|---|
|Bibliographic|G. Oligeri, S. Sciancalepore, S. Raponi, R. Di Pietro ·<br>2023 · IEEE Trans. Information Forensics and<br>Security, vol. 18, pp. 274–289 ·<br>DOI<br>10.1109/TIFS.2022.3219287 ·<br>arXiv:2010.05470 · [PR]<br>[SAT] · Data (public, per authors)|
|Problem / attack model|Physical-layer authentication of LEO satellites<br>against spoofing and replay|
|Data / input|Measurement campaign of over 100 M IQ samples<br>from Iridium (<br>arXiv)|
|Features / model|IQ samples rendered as images; CNN and<br>autoencoder|
|Method / metrics|Two scenarios: intra-constellation classification<br>and authentication with minimal satellite<br>knowledge; accuracy|
|Results|Accuracy between 0.8 and 1 depending on<br>assumptions|
|Limitations|The authors note that the number of IQ samples<br>required, combined with low satellite link<br>bandwidth, may prevent spoofing detection in<br>some configurations (<br>HBKU record)|
|Relevance|Independent confirmation of SatIQ's premise and<br>an honest statement of the**detection-latency vs**<br>**sample-budget trade-off**|



#### **B3. FadePrint: Satellite Spoofing Detection via Fading Fingerprinting** 

|Field|Detail|
|---|---|
|Bibliographic|G. Oligeri, S. Sciancalepore, A. Sadighian · 2024 ·<br>IEEE CCNC ·<br>author PDF · [PR][SAT]|
|Problem / attack model|Spoofing and replay from a terrestrial (indoor)<br>transmitter; all higher-layer authentication<br>assumed broken|



Page 11 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Data / input|Iridium ring-alert recordings from the PAST-AI<br>campaign: 589 h, 102,318,546 IQ samples, 66<br>satellites; plus five 10-minute indoor non-line-of-<br>sight transmitter streams sending the same QPSK<br>bits (emulated replay)|
|Features / model|Bivariate IQ histogram of 10,000-sample batches →<br>225 × 225 image; FCDD (Fully Convolutional Data<br>Description), an explainable one-class CNN|
|Method / metrics|Train on 64 satellites; calibrate the threshold on<br>one held-out satellite plus terrestrial data; test on<br>unseen satellite and terrestrial streams over 100<br>permutations; accuracy and confusion matrix|
|Results|Accuracy above 0.99, with 19 false positives out of<br>150,370 test samples (<br>PDF)|
|Limitations|Threshold calibration uses attack (terrestrial)<br>samples, so it is not purely one-class; only indoor<br>NLoS spoofers, no outdoor or line-of-sight<br>attacker; the 10,000-sample batch sets detection<br>latency|
|Relevance|Turns fading — the main false-positive source —<br>into a**feature**, with no per-transmitter retraining;<br>the strongest evidence that a one-class IQ model<br>separates satellite from terrestrial links, and a<br>template for a calibrated, explainable one-class<br>branch|



#### **B4. Sticky Fingers: Resilience of Satellite Fingerprinting against Jamming Attacks** 

|Field|Detail|
|---|---|
|Bibliographic|J. Smailes, E. Salkield, S. Köhler, S. Birnbach, M.<br>Strohmeier, I. Martinovic · 2024 · NDSS SpaceSec<br>workshop ·<br>DOI 10.14722/spacesec.2024.23054 ·<br>arXiv:2402.05042 · [WS][SAT]|
|Problem / attack model|Can a jammer make legitimate messages fail<br>fingerprint authentication (denial of service via<br>false rejection)?|



Page 12 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Data / input|New dataset: Gaussian noise and tone jamming<br>added to Iridium signals at varying power|
|Features / model|Pre-trained SatIQ model|
|Method / metrics|Fingerprint error vs jamming power, compared with<br>decoder failure|
|Results|Disrupting the fingerprint takes about the same<br>jamming power as jamming the message itself<br>(<br>abstract)|
|Limitations|Only two jamming types; non-adaptive jammer|
|Relevance|Directly answers a reviewer's question:**does**<br>**adding a detector create a new DoS surface?**<br>Replicate this experiment for your own detector|



#### **B5. Satellite Spoofing from A to Z: On the Requirements of Satellite Downlink Overshadowing Attacks** 

|Field|Detail|
|---|---|
|Bibliographic|E. Salkield, M. Szakály, J. Smailes, S. Birnbach, S.<br>Köhler, M. Strohmeier, I. Martinovic · 2023 · ACM<br>WiSec (best paper runner-up) ·<br>DOI<br>10.1145/3558482.3590190 ·<br>PDF · [PR][SAT]|
|Problem / attack model|What does a downlink spoofer need? Antenna<br>sidelobe targeting, overshadowing, AGC takeover|
|Data / input|Systematic review plus experiments|
|Features / model|No ML|
|Method / metrics|Attack requirement analysis|
|Results|Protocols built to be robust against channel noise<br>turned out significantly less robust against an<br>overshadowing attacker (<br>ORA)|
|Limitations|Not a detector|
|Relevance|Defines a**realistic spoofing attacker model**for<br>your threat table; warns that FEC robustness does<br>not imply security|



Page 13 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **B6. Intermittent Jamming against Telemetry and Telecommand of Satellite Systems and a Learning-driven Detection Strategy** 

|Field|Detail|
|---|---|
|Bibliographic|S. Gecgel, G. Karabulut Kurt · 2021 · ACM WiseML<br>workshop ·<br>DOI 10.1145/3468218.3469041 ·<br>arXiv:2107.06181 · [WS][SAT]|
|Problem / attack model|Jamming of TT&C: barrage, pilot-tone, and<br>**intermittent jamming that follows telemetry**<br>**content patterns**|
|Data / input|Simulated conceptual OFDM TT&C signal, BPSK,<br>Rician channel (K = 5), SNR 5/10/15 dB, SJR −20 to 0<br>dB; 2,000 train / 3,000 test per dataset(<br>arXiv)|
|Features / model|STFT images (96 × 96); lightweight CNN vs PCA (45<br>components) + linear SVM|
|Method / metrics|Binary jammed / not jammed; accuracy|
|Results|Intermittent jamming: CNN 80.4 / 88.8 / 93.1% vs<br>SVM 83.4 / 84.7 / 86.1% at 5 / 10 / 15 dB SNR; all<br>attacks: CNN 92.1 / 99.5 / 99.7%|
|Limitations|Conceptual OFDM, not a CCSDS TT&C waveform;<br>no benign-degradation class; accuracy only, no FPR<br>or latency|
|Relevance|The**only TT&C-specific ML jamming detector**<br>**found**; its gaps (no degradation class, no FPR) are<br>exactly what your project can fix|



#### **B7. Detecting Spacecraft Anomalies Using LSTMs and Nonparametric Dynamic Thresholding (Telemanom)** 

|Field|Detail|
|---|---|
|Bibliographic|K. Hundman, V. Constantinou, C. Laporte, I. Colwell,|
||T. Soderstrom · 2018 · ACM KDD ·<br>DOI|
||10.1145/3219819.3219845 ·<br>arXiv:1802.04431 · [PR]|
||[SAT] · Code + Data (<br>telemanom)|
|Problem / attack model|Telemetry anomalies (faults, not attacks)|



Page 14 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Data / input|Expert-labelled telemetry from SMAP satellite and<br>MSL Curiosity rover|
|Features / model|Per-channel LSTM forecaster|
|Method / metrics|Nonparametric, unsupervised dynamic threshold<br>on smoothed residuals, plus explicit false-positive<br>mitigation (pruning) (<br>arXiv)|
|Results|105 labelled anomaly sequences over 82 channels.<br>With pruning: precision 87.5%, recall 80.0%, F0.5<br>0.71. Without pruning, precision fell to 48.9% (recall<br>84.8%). The authors state the false-positive rate<br>was the main obstacle to operational adoption<br>([arXiv](https://arxiv.org/pdf/1802.04431))|
|Limitations|SMAP/MSL have been criticised as flawed<br>benchmarks (trivial anomalies, mislabelling) by<br>Wu<br>& Keogh, IEEE TKDE|
|Relevance|Reuse the**dynamic-threshold and pruning logic**<br>for any score stream (IQ, frame, telemetry); not the<br>dataset as a headline benchmark|



#### **B8. European Space Agency Benchmark for Anomaly Detection in Satellite Telemetry (ESA-ADB)** 

|Field|Detail|
|---|---|
|Bibliographic|K. Kotowski, C. Haskamp, J. Andrzejewski, B.<br>Ruszczak, J. Nalepa, D. Lakey, P. Collins, A. Kolmas,<br>M. Bartesaghi, J. Martinez-Heras, G. De Canio ·<br>2024 ·<br>arXiv:2406.17826;journal version in Journal<br>of Data-centric Machine Learning Research (<br>PDF)·<br>[PR][SAT] · Code(<br>GitHub)|
|Problem / attack model|Operational telemetry anomalies; telecommands<br>included as timestamped events|
|Data / input|Annotated real telemetry from three ESA missions,<br>two in the benchmark|
|Features / model|Benchmarks typical TSAD algorithms|



Page 15 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Method / metrics|Hierarchical evaluation pipeline with metrics<br>designed for operators|
|Results|Typical algorithms fall short of operators' needs;<br>new approaches are necessary (<br>arXiv)|
|Limitations|Faults, not cyberattacks; large|
|Relevance|Use its**evaluation philosophy**(event-wise,<br>operator-oriented metrics) and, optionally, its<br>telemetry for a data-level injection experiment|



#### **B9. Satellite network intrusion detection on the STIN dataset (representative)** 

|Field|Detail|
|---|---|
|Bibliographic|"Deep Learning Based Hybrid Intrusion Detection<br>Systems to Protect Satellite Networks" · 2023 · J.<br>Network and Systems Management ·<br>DOI<br>10.1007/s10922-023-09767-8 · [PR][SAT-network]|
|Problem / attack model|Network-flow intrusions (mostly DoS) in satellite-<br>terrestrial integrated networks|
|Data / input|STIN (satellite) and UNSW-NB15 (terrestrial) flow<br>features|
|Features / model|Feature selection + RF and hybrid DL|
|Method / metrics|Accuracy|
|Results|SFS-RF reached 90.5% on STIN with 10 selected<br>features vs 85.41% with all (<br>record); other STIN<br>papers report 99.99% (<br>example)|
|Limitations|IP-flow level, not the RF ground link; near-saturated<br>accuracies suggest an easy or leaky benchmark|
|Relevance|Mainly a**cautionary example**: do not anchor Track 1<br>on IP-flow datasets|



Context papers worth citing but not detectors: Pavur & Martinovic, SoK on satellite cyber- <u>security research (arXiv:2010.10872)</u> [PP][SAT]; the ESA SPACE-SHIELD and Aerospace <u>SPARTA</u> threat matrices, which list record-and-replay of TC/TM as a technique and SDLS sequence numbers as the mitigation. 

Page 16 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

### Category C — Spoofing detection 

Satellite-link spoofing detection outside GNSS is essentially B1–B3 above. The GNSS literature is far larger and contributes the key idea of **monitoring signal distortion jointly with power** , plus the TEXBAT benchmark. 

#### **C1. GNSS Spoofing and Detection** 

|Field|Detail|
|---|---|
|Bibliographic|M. L. Psiaki, T. E. Humphreys · 2016 · Proceedings of<br>the IEEE, vol. 104 no. 6 ·<br>DOI<br>10.1109/JPROC.2016.2526658 · [PR][GNSS] (survey)|
|Problem / attack model|Taxonomy of simplistic, intermediate and<br>sophisticated spoofers; meaconing|
|Data / input|Survey|
|Features / model|Covers power monitoring, signal-quality<br>monitoring, angle-of-arrival, cryptographic<br>authentication|
|Method / metrics|Survey|
|Results|Framework for layered defence|
|Limitations|Pre-dates most ML work|
|Relevance|Use its**attacker capability ladder**to structure<br>your spoofing threat model|



#### **C2. GNSS Signal Authentication via Power and Distortion Monitoring** 

|Field|Detail|
|---|---|
|Bibliographic|K. D. Wesson, J. N. Gross, T. E. Humphreys, B. L.<br>Evans · IEEE Trans. Aerospace and Electronic<br>Systems (2018) ·<br>arXiv:1702.06554 · [PR][GNSS]|
|Problem / attack model|Spoofing, including sophisticated spoofers with<br>aligned code/carrier|
|Data / input|27 recordings: 6 spoofing (TEXBAT), 14 multipath-<br>dense, 4 jamming, 3 clean(<br>arXiv)|



Page 17 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Features / model|Received power + correlation-function distortion;<br>statistical (Bayesian/likelihood) detector, no deep<br>learning|
|Method / metrics|Hypothesis testing with explicit multipath and<br>jamming hypotheses|
|Results|Separates spoofing from multipath and jamming on<br>empirical recordings (see paper for ROC)|
|Limitations|GNSS correlator outputs; not ML|
|Relevance|**The methodological template for H_0 / H_D / H_A**:<br>evaluate on benign-degradation recordings, not<br>only clean vs attack|



Page 18 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **C3. Use of Supervised Machine Learning for GNSS Signal Spoofing Detection with Validation on Real-World Meaconing and Spoofing Data (Parts I and II)** 

|Field|Detail|
|---|---|
|Bibliographic|S. Semanjski, I. Semanjski, W. De Wilde, A. Muls ·<br>2020 · Sensors 20(4):1171 and 20(7):1806 ·<br>DOI<br>10.3390/s20041171 ·<br>DOI 10.3390/s20071806 · [PR]<br>[GNSS]|
|Problem / attack model|Spoofing (data altered) and meaconing (genuine<br>data re-broadcast, i.e. replay)|
|Data / input|Lab-generated spoofing for training; two real-world<br>events for validation|
|Features / model|Cross-correlation of multiple GNSS observables<br>and measurements; C-SVM|
|Method / metrics|Train on lab data, validate on real events; Part II<br>cross-feeds meaconing and spoofing|
|Results|Validated on real meaconing and spoofing events<br>(<br>PMC)|
|Limitations|Receiver-observable features, specific to GNSS|
|Relevance|Shows**lab-to-real validation**, and treats meaconing<br>as replay; mirror this with "train on simulated, test<br>on recorded"|



#### **C4. Texas Spoofing Test Battery (TEXBAT) and ML on it** 

|Field|Detail|
|---|---|
|Bibliographic|TEXBAT dataset, Radionavigation Lab, UT Austin<br>(<br>page)· [DS][GNSS]; example ML use: "Leveraging<br>machine learning for the detection of structured<br>interference in GNSS", PeerJ Computer Science<br>2024,<br>DOI 10.7717/peerj-cs.2399 [PR][GNSS]|
|Problem / attack model|Time and position push spoofing, including secure-<br>code-estimation-and-replay (SCER) in scenario<br>ds8|



Page 19 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Data / input|16-bit complex IQ, 25 Msps, 20 MHz around GPS L1,<br>static and dynamic, with clean references (<br>Wesson<br>et al.)|
|Features / model|Varies: SQM metrics, C/N0, ML classifiers|
|Method / metrics|Detection rate on scenarios|
|Results|Many detectors work on simplistic/intermediate<br>scenarios but fail against careful power and time<br>management (<br>PeerJ via PMC)|
|Limitations|Some scenarios carry quantisation/aliasing<br>artefacts that make them easier than intended<br>(<br>Wesson et al.)|
|Relevance|Real IQ attack recordings with matched clean<br>baselines; ideal for testing whether a model learns<br>the attack or the recording artefact|



#### **What transfers from GNSS to a generic satellite ground link** 

|GNSS technique|Transfers?|How it maps to a ground link|
|---|---|---|
|Power + distortion joint<br>monitoring|Yes|Received power vs EVM /<br>constellation spread / sync-<br>correlation peak shape per frame|
|Signal-quality monitoring of<br>correlator peak|Partly|Use the frame sync-word<br>correlation peak instead of PRN<br>correlators|
|Cross-check of observables<br>(Semanjski)|Yes|Cross-check SNR, Doppler, frame<br>error rate and timestamp drift<br>against the pass prediction|
|Cryptographic navigation-<br>message authentication|Conceptually|Equivalent is CCSDS SDLS<br>authentication; detector covers<br>links without it|
|Multi-antenna angle of arrival|No (hardware)|Out of scope for this phase|
|RF fingerprinting of transmitter|Yes,<br>demonstrated|SatIQ, PAST-AI on Iridium|



Page 20 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

### Category D — Jamming / interference detection 

Supervised spectrogram classifiers reach 90–98% on GNSS jammer types, but the 2024– 2025 Fraunhofer work shows these numbers collapse under dataset shift. Unsupervised methods (A3, A4) are less accurate on known classes but are the only ones that cover unseen jammers. 

#### **D1. The Feasibility of Launching and Detecting Jamming Attacks in Wireless Networks** 

|Field|Detail|
|---|---|
|Bibliographic|W. Xu, W. Trappe, Y. Zhang, T. Wood · 2005 · ACM<br>MobiHoc, pp. 46–57 ·<br>DOI 10.1145/1062689.1062697 ·<br>[PR][GEN] (seminal)|
|Problem / attack model|Constant, deceptive, random and reactive jammers|
|Data / input|MICA2 mote testbed|
|Features / model|Signal strength, carrier-sensing time, packet<br>delivery ratio (PDR); statistical consistency checks|
|Method / metrics|Signal-strength consistency and location<br>consistency checks for poor PDR|
|Results|No single statistic separates jamming from poor<br>links; consistency checks do (<br>PDF)|
|Limitations|Old; sensor networks|
|Relevance|**Foundational evidence for the degradation-vs-**<br>**attack principle**; still the cleanest explainable rule<br>to fuse with ML|



#### **D2. Jammer Classification in GNSS Bands via Machine Learning Algorithms** 

|Field|Detail|
|---|---|
|Bibliographic|R. Morales Ferre, A. de la Fuente, E. S. Lohan · 2019 ·<br>Sensors 19(22):4841 ·<br>DOI 10.3390/s19224841 · [PR]<br>[GNSS] · Data (open access)|
|Problem / attack model|Classify five jammer types plus no-jammer|
|Data / input|Simulated jammed GNSS signals → time-frequency<br>black-and-white images|
|Features / model|SVM and CNN on images|



Page 21 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Method / metrics|Six-class accuracy|
|Results|SVM up to 94.90%, CNN up to 91.36% (<br>PMC)|
|Limitations|Simulated; no FPR or latency|
|Relevance|Evidence that**classical ML can match or beat a**<br>**CNN**on spectrogram-based jammer typing;<br>justifies Architecture 1|



#### **D3. Few-Shot Learning with Uncertainty-based Quadruplet Selection for Interference Classification in GNSS Data** 

|Field|Detail|
|---|---|
|Bibliographic|F. Ott, L. Heublein, N. L. Raichur, T. Feigl, J. Hansen,<br>A. Rügamer, C. Mutschler · 2024 · ICL-GNSS ·<br>arXiv:2402.09466 · [PR][GNSS] · Data (<br>IEEE<br>DataPort)|
|Problem / attack model|Adapt to new, unseen interference classes from<br>few examples|
|Data / input|Real recordings at a German motorway bridge: 8<br>interference + 3 non-interference classes, E1 and<br>E6 snapshots|
|Features / model|Metric learning with quadruplet loss and<br>uncertainty-based sample selection|
|Method / metrics|Few-shot classification accuracy|
|Results|97.66% jammer classification accuracy (<br>arXiv)|
|Limitations|GNSS band; spectral snapshots rather than raw<br>continuous IQ|
|Relevance|**Embedding + few-shot**is the right answer to "what<br>about a jammer you never trained on?"; same<br>metric-learning idea as SatIQ|



Page 22 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **D4. Evaluation of (Un-)Supervised ML Methods for GNSS Interference Classification with Real-World Data Discrepancies** 

|Field|Detail|
|---|---|
|Bibliographic|N. L. Raichur, F. Ott, et al. (Fraunhofer IIS) · 2025 ·<br>arXiv:2503.23775 · [PP][GNSS]. Companion: L.<br>Heublein et al., "Evaluating ML Robustness in GNSS<br>Interference Classification, Characterization &<br>Localization",<br>arXiv:2409.15114 [PP]|
|Problem / attack model|Do supervised interference classifiers survive real-<br>world noise, multipath and dataset mixing?|
|Data / input|Multiple Fraunhofer real-world and controlled<br>datasets|
|Features / model|Supervised SOTA (ResNet18 features), pseudo-<br>labelling, outlier detection, domain adaptation,<br>augmentation|
|Method / metrics|Cross-dataset evaluation|
|Results|Combining datasets is hard because of data<br>discrepancies; outlier detection, domain<br>adaptation and augmentation are needed to adapt<br>(<br>Fraunhofer)|
|Limitations|Preprints|
|Relevance|**Direct evidence that offline accuracy overstates**<br>**field performance**; justifies cross-recording test<br>splits in your benchmark|



### Category E — Replay / sequence attack detection 

The evidence is thin and split. Freshness is solved cryptographically by SDLS sequence numbers when SDLS is deployed. Without it, replay detection relies on physical-layer fingerprinting (B1, B2) or on statistical tests of protocol behaviour. **We found no peerreviewed ML paper that detects replay or sequence manipulation on CCSDS frames.** 

Page 23 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **E1. CCSDS Space Data Link Security (SDLS) anti-replay** 

|Field|Detail|
|---|---|
|Bibliographic|CCSDS 355.0-B (first issue Sept. 2015; adopted as<br>ISO 21324) ·<br>overview · standard, not a paper · [SAT]|
|Problem / attack model|Record-and-replay of TC/TM frames (<br>SPACE-<br>SHIELD T2008.006)|
|Data / input|Frame security header|
|Features / model|Authenticated anti-replay sequence number in a<br>window|
|Method / metrics|Deterministic|
|Results|SPACE-SHIELD lists SDLS sequence numbers as<br>the mitigation and notes increasing counters also<br>expose unauthorised messages (<br>M2004)|
|Limitations|Legacy and many small missions do not use SDLS;<br>window misconfiguration and resets cause false<br>rejections|
|Relevance|Your**rule layer**: implement window checks exactly,<br>then let ML handle what rules cannot (legacy links,<br>physical replay)|



#### **E2. Misbehaviour Detection Algorithm in CCSDS Space Telecommand Link** 

|Field|Detail|
|---|---|
|Bibliographic|Zhang Lei, An Cheng-jin, Zhang Quan, Tang Chao-<br>jing · 2010 · Journal of Electronics & Information<br>Technology (JEIT), vol. 32 no. 2, pp. 290–295 ·<br>DOI<br>10.3724/SP.J.1146.2009.01020 · [PR][SAT] (in<br>Chinese, English abstract)|
|Problem / attack model|Attacks against TC transmission that exploit COP-1<br>retransmission; authentication failures look like<br>channel errors|
|Data / input|Simulation with a space fading channel model|
|Features / model|Retransmission-request behaviour; hypothesis test<br>against attacker and channel models|



Page 24 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Method / metrics|Modified retransmission mechanism + detection<br>test|
|Results|Authors report detection without false alarm<br>across channel states in simulation|
|Limitations|Simulation only; regional journal; dated|
|Relevance|Rare paper that**models channel errors and**<br>**attacks jointly at the protocol level**— exactly the<br>H_D vs H_A problem|



#### **E3. DeepLog: Anomaly Detection and Diagnosis from System Logs through Deep Learning** 

|Field|Detail|
|---|---|
|Bibliographic|M. Du, F. Li, G. Zheng, V. Srikumar · 2017 · ACM CCS ·<br>DOI 10.1145/3133956.3134015 · [PR][GEN]|
|Problem / attack model|Anomalous event sequences in system logs|
|Data / input|Parsed log-key sequences + parameter values|
|Features / model|LSTM next-key predictor; anomaly = true next key<br>not in top-g predictions|
|Method / metrics|Precision, recall, F1|
|Results|Strong results on HDFS and OpenStack logs (see<br>paper)|
|Limitations|Logs, not radio frames|
|Relevance|**Directly transferable to frame/command-type**<br>**sequences**: model the sequence of (APID, frame<br>type, command opcode) and flag out-of-grammar<br>or out-of-order events; top-g gives an explainable<br>reason|



Page 25 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **E4. Secure Control Against Replay Attacks** 

|Field|Detail|
|---|---|
|Bibliographic|Y. Mo, B. Sinopoli · 2009 · Allerton Conference ·<br>DOI<br>10.1109/ALLERTON.2009.5394956 · [PR][GEN]<br>(seminal)|
|Problem / attack model|Attacker records sensor outputs and replays them|
|Data / input|Control-system measurements|
|Features / model|Inject a small known random watermark; chi-<br>square detector|
|Method / metrics|Detection probability vs control cost|
|Results|Replay becomes detectable because the replayed<br>data lacks the current watermark|
|Limitations|Control systems, not links|
|Relevance|Idea for**active replay detection**in the testbed: a<br>pseudo-random, per-pass physical or timing<br>signature the replay cannot contain|



Physical-layer replay detection itself is covered by B1 (SatIQ replay EER 0.120) and B2. Telemetry-level staleness detection can borrow B7's forecasting residuals: replayed telemetry is internally consistent but inconsistent with the predicted current state. 

### Category F — Adversarial machine learning against RF detectors 

Raw-IQ deep classifiers are easy to evade when the attacker can add a perturbation to the classifier input, but over-the-air channel and receiver effects weaken those attacks, and the attacker must still deliver a working payload. Almost all work attacks modulation classifiers; **we found no paper that attacks a satellite ground-link anomaly detector** . 

**F1. Adversarial Attacks on Deep-Learning Based Radio Signal Classification** 

|Field|Detail|
|---|---|
|Bibliographic|M. Sadeghi, E. G. Larsson · 2019 · IEEE Wireless<br>Communications Letters ·<br>arXiv:1808.07713 · [PR]<br>[GEN]|
|Problem / attack model|White-box and universal black-box evasion against<br>a modulation classifier|



Page 26 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Data / input|RadioML (GNU Radio) dataset, raw IQ|
|Features / model|CNN classifier; FGSM-style and universal<br>perturbations|
|Method / metrics|Accuracy vs perturbation power, compared with<br>jamming of equal power|
|Results|Very small perturbations sharply cut accuracy; far<br>more effective than classical jamming at equal<br>power(<br>arXiv)|
|Limitations|Direct input access; no channel|
|Relevance|Upper bound on attacker power; motivates not<br>relying on a single DL score|



#### **F2. Evaluating Adversarial Evasion Attacks in the Context of Wireless Communications** 

|Field|Detail|
|---|---|
|Bibliographic|B. Flowers, R. M. Buehrer, W. C. Headley · 2020 ·<br>IEEE TIFS vol. 15, pp. 1102–1113 ·<br>arXiv:1903.01563 ·<br>[PR][GEN]|
|Problem / attack model|Three attacker positions: direct input access,<br>synchronous over-the-air, asynchronous from a<br>separate device|
|Data / input|Raw IQ modulation classification|
|Features / model|FGSM|
|Method / metrics|Success judged by**bit error rate of the intended**<br>**link**, not only misclassification|
|Results|RF classifiers are vulnerable even over the air, but<br>receiver effects significantly impair evasion(<br>arXiv)|
|Limitations|FGSM only|
|Relevance|Gives the**threat-model vocabulary**and the right<br>success metric for a testbed: evasion only counts if<br>the malicious payload still decodes|



Page 27 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **F3. Robust Adversarial Attacks Against DNN-Based Wireless Communication Systems** 

|Field|Detail|
|---|---|
|Bibliographic|A. Bahramali, M. Nasr, A. Houmansadr, D. Goeckel,<br>D. Towsley · 2021 · ACM CCS ·<br>DOI<br>10.1145/3460120.3484777 ·<br>arXiv:2102.00918 · [PR]<br>[GEN]|
|Problem / attack model|Input-agnostic, channel-robust perturbations<br>against DNN wireless systems|
|Data / input|Wireless DNN tasks incl. modulation recognition|
|Features / model|Learned perturbation generator robust to channel<br>effects|
|Method / metrics|Attack success under channel variation|
|Results|Robust, input-agnostic attacks are feasible (see<br>paper)|
|Limitations|Terrestrial|
|Relevance|Strongest**top-tier security-venue**evidence that<br>over-the-air adversarial attacks are practical; cite<br>in the threat model|



#### **F4. Adversarial Machine Learning in Wireless Communications Using RF Data: A Review** 

|Field|Detail|
|---|---|
|Bibliographic|D. Adesina, C.-C. Hsieh, Y. E. Sagduyu, L. Qian · 2023<br>· IEEE Communications Surveys & Tutorials, vol. 25<br>no. 1, pp. 77– ·<br>arXiv:2012.14392 · [PR][GEN]<br>(survey)|
|Problem / attack model|Evasion, poisoning, inference attacks in wireless;<br>detection and mitigation|
|Relevance|Use as the**map of attacks and defences**; cites<br>spectrum-data poisoning (<br>Shi et al.,<br>arXiv:1901.09247), relevant if your detector retrains<br>online|



Page 28 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### **F5. A Hybrid Training-Time and Run-Time Defense Against Adversarial Attacks in Modulation Classification** 

|Field|Detail|
|---|---|
|Bibliographic|L. Zhang, S. Lambotharan, G. Zheng, G. Liao, A.<br>Demontis, F. Roli · 2022 · IEEE Wireless<br>Communications Letters ·<br>arXiv:2407.06807 · [PR]<br>[GEN]|
|Problem / attack model|White-box evasion against AMC|
|Data / input|RML2016.10a|
|Features / model|Customised adversarial training + run-time**neural**<br>**rejection**of out-of-distribution inputs|
|Method / metrics|Accuracy under attack|
|Results|Outperforms earlier defences on RML2016.10a<br>(<br>arXiv)|
|Limitations|Classification task, small dataset|
|Relevance|Rejection is the right primitive for a security<br>detector:**"I don't know" escalates instead of**<br>**silently misclassifying**|



#### **F6. Towards Deep Learning Models Resistant to Adversarial Attacks (PGD adversarial training)** 

|Field|Detail|
|---|---|
|Bibliographic|A. Madry, A. Makelov, L. Schmidt, D. Tsipras, A.<br>Vladu · 2018 · ICLR ·<br>arXiv:1706.06083 · [PR][GEN]<br>(seminal)|
|Relevance|Standard robust-training baseline; RF-specific<br>application:<br>SafeAMC, arXiv:2105.13746 [PP]|



**How an attacker could fool your detector, and what the literature says helps** 

Page 29 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Attack on the detector|Feasibility in a testbed|Defence with literature<br>support|
|---|---|---|
|Evasion of IQ classifier by<br>small perturbation|High with input access (<br>F1);<br>reduced over the air (<br>F2)|Adversarial training (<br>F6);<br>run-time rejection (<br>F5)|
|Mimicry of normal spectrum<br>/ slow ramp-up jamming|Medium|Fuse independent evidence<br>(physical consistency rules,<br>D1) so one score cannot be<br>gamed alone|
|Induce false rejections (DoS<br>via detector)|Medium|Measure it as in<br>Sticky<br>Fingers|
|Poison online retraining with<br>crafted "normal" data|Low in a student testbed|Freeze baseline models; gate<br>retraining on human-<br>confirmed data (<br>F4)|



### Category G — Edge / lightweight AI 

Compression of RF classifiers is well studied for modulation recognition; compression of **RF anomaly detectors** is not. MLPerf Tiny is the only peer-reviewed benchmark that fixes an anomaly-detection workload on microcontroller-class devices. 

#### **G1. MLPerf Tiny Benchmark** 

|Field|Detail|
|---|---|
|Bibliographic|C. Banbury, V. Janapa Reddi, et al. · 2021 · NeurIPS<br>Datasets and Benchmarks ·<br>proceedings ·<br>arXiv:2106.07597 · [PR][GEN] · Code|
|Problem|Reproducible accuracy / latency / energy<br>benchmarking of TinyML|
|Data / input|Anomaly-detection task uses DCASE2020<br>(ToyADMOS + MIMII) audio as 5 × 128 log-mel<br>frames|
|Features / model|Fully-connected autoencoder; reconstruction-<br>error anomaly score|
|Method / metrics|AUC quality target 0.85; latency and energy<br>measured on device(<br>arXiv)|



Page 30 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Field|Detail|
|---|---|
|Relevance|A**ready-made template for an edge AE on**|
||**spectral frames**: replace log-mel with log-PSD|
||frames and report the same three metrics|



#### **G2. RFNet: Fast and Efficient Neural Network for Modulation Classification of Radio Frequency Signals** 

|Field|Detail|
|---|---|
|Bibliographic|2022 · ITU Journal on Future and Evolving<br>Technologies, vol. 3 issue 2 ·<br>ITU · [PR][GEN]|
|Features / model|Multiscale and depthwise-separable convolutions;<br>pruning and quantisation-aware training|
|Results|RFNet-32++: 56.09% average and 92.21% at +20 dB<br>on RadioML 2018 with only 3.1K parameters(<br>ITU)|
|Relevance|Shows an IQ CNN can be**three orders of**<br>**magnitude smaller**than standard ResNets while<br>staying useful at high SNR|



#### **G3. Compression methods (general and RF)** 

|Paper|Status|Key point for us|
|---|---|---|
|Jacob et al., Quantization and<br>Training of NNs for Efficient<br>Integer-Arithmetic-Only Inference,<br>CVPR 2018|[PR][GEN]|INT8 post-training / QAT recipe<br>used by TFLite|
|Hinton, Vinyals, Dean, Distilling the|[PP][GEN]|Teacher–student compression|
|Knowledge in a Neural Network,<br>2015|seminal||
|Edge-Efficient Deep Learning<br>Models for AMC: A Performance|[PP][GEN]|Compares pruning, quantisation,<br>KD and combinations for AMC|
|Analysis, 2024||CNNs|
|Byeon et al., Pruned and Quantized<br>Hybrid Models for Edge-Based<br>AMR, IEEE IoT Journal 2026|[PR][GEN]|Up to 91% memory reduction after<br>pruning + dynamic-range<br>quantisation|



Page 31 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Paper|Status|Key point for us|
|---|---|---|
|ClST with signal knowledge|[PR][GEN]|Distillation tailored to signals|
|distillation, IEEE TWC|||



**Explainability (cross-cutting).** SAIFE (A3) gives interpretable latent features; DeepLog (E3) gives "expected next event" explanations; for tabular feature models, SHAP (Lundberg & Lee, NeurIPS 2017) [PR][GEN] gives per-feature attributions. FadePrint (B3) uses FCDD, an explainable one-class network that yields anomaly heatmaps on its IQhistogram images. We found no satellite-link paper evaluating explanation quality for attack detection. 

## 4. Comparing the ML methods 

False-positive rate and latency are **missing from most papers** , which is itself a finding: only SAIFE (CFAR 1%), SatIQ (EER), Tandiya et al. (false-alarm rate) and MLPerf Tiny (latency) report what an operator needs. "n.r." = not reported in the sources checked. 

### 4.1 Paper-level comparison 

|Paper|Attack|Input|Features|Model|Sup./Unsup<br>.|Dataset|Accuracy<br>/ F1 / AUC|FPR|Latency|Comput<br>e|Main<br>limitation|
|---|---|---|---|---|---|---|---|---|---|---|---|
|SatIQ<br>(B1)|Spoof,<br>replay|IQ @ 25<br>MS/s|Learned<br>embeddi<br>ng|AE +<br>Siamese|Metric<br>learning<br>(normal-<br>only<br>enrolment)|Iridium,<br>public|AUC<br>0.946|EER<br>0.120|n.r.|High<br>(high-<br>rate IQ)|Needs<br>high<br>sample<br>rate|
|PAST-AI<br>(B2)|Spoof,<br>replay|IQ<br>images|Learned|CNN, AE|Sup. + AE|Iridium,<br>public|Acc. 0.8–1|n.r.|Sample-<br>hungry|Medium|IQ budget<br>vs link<br>bandwidt<br>h|
|Gecgel &<br>Kurt (B6)|TT&C<br>jamming|STFT<br>96×96|Learned<br>/ PCA|CNN vs<br>SVM|Sup.|Simulate<br>d OFDM|80–99.7%|n.r.|n.r.|Low|No<br>degradati<br>on class|
|SAIFE<br>(A3)|Spectrum<br>anomaly|PSD|Interpret<br>able<br>latent|Adversar<br>ial AE|Unsup.<br>(+semi)|Electrose<br>nse +<br>synthetic|>80% det.|1%<br>(CFAR)|n.r.|Low–<br>medium|PSD only|
|Saifalda<br>wla (A4)|Interfere<br>nce|Time/fre<br>q signal|Learned|AE, VAE,<br>Transfor<br>mer|Unsup.<br>(normal-<br>only)|Emulated<br>NGSO/G<br>SO|Up to<br>+31.23%<br>vs energy<br>det.|n.r.|n.r.|High<br>(Transfor<br>mer)|Non-<br>adversari<br>al,<br>simulated|
|Tandiya<br>(A2)|Jam,<br>hijack,<br>chirp|Spectrog<br>ram, SCF|Learned|PredNet|Unsup.|Own|Detection<br>ratio|Reporte<br>d FAR|n.r.|High|Heavy<br>model|
|Morales<br>Ferre<br>(D2)|GNSS<br>jamming<br>type|TF image|Pixels|SVM,<br>CNN|Sup.|Simulate<br>d, public|94.90% /<br>91.36%|n.r.|n.r.|Low|Simulated|
|Ott (D3)|Unseen<br>interfere<br>nce|Spectral<br>snapsho<br>t|Embeddi<br>ng|Quadrupl<br>et FSL|Few-shot|Real<br>highway,<br>public|97.66%|n.r.|n.r.|Medium|GNSS<br>band|



Page 32 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Paper|Attack|Input|Features|Model|Sup./Unsup<br>.|Dataset|Accuracy<br>/ F1 / AUC|FPR|Latency|Comput<br>e|Main<br>limitation|
|---|---|---|---|---|---|---|---|---|---|---|---|
|Semanjs<br>ki (C3)|Spoof,<br>meacon|GNSS<br>observa<br>bles|Cross-<br>correlati<br>ons|C-SVM|Sup.|Lab + 2<br>real<br>events|See paper|n.r.|n.r.|Low|GNSS-<br>specific|
|Wesson<br>(C2)|Spoof vs<br>multipath<br>vs jam|Correlat<br>or +<br>power|Power,<br>distortio<br>n|Statistica<br>l test|Model-<br>based|TEXBAT +<br>21 others|ROC<br>(paper)|Controll<br>ed|Low|Very low|Not ML|
|Hundma<br>n (B7)|Telemetr<br>y<br>anomaly|Telemetr<br>y series|Forecast<br>residual|LSTM +<br>NDT|Unsup.|SMAP/M<br>SL, public|P/R<br>(paper)|Pruned|n.r.|Low|Benchmar<br>k flaws|
|DeepLog<br>(E3)|Sequenc<br>e<br>anomaly|Event<br>keys|Next-<br>event|LSTM|Unsup.|HDFS,<br>OpenSta<br>ck|F1 (paper)|n.r.|n.r.|Low|Logs, not<br>frames|
|MLPerf<br>Tiny AD<br>(G1)|Generic<br>anomaly|Log-mel<br>frames|640-dim<br>input|FC-AE|Unsup.|ToyADM<br>OS|AUC ≥<br>0.85<br>target|n.r.|Measure<br>d on<br>MCU|Very low|Audio<br>domain|



### 4.2 Method-level comparison for this project 

|Method|Advantages|Disadvantages|Suitability for our<br>project|
|---|---|---|---|
|Statistical (energy<br>detector, CUSUM,<br>chi-square,<br>consistency<br>checks)|Explainable, near-zero<br>compute, calibrated<br>FAR, no training data|Misses low-power<br>and structured<br>attacks; thresholds<br>drift with geometry|**Essential**as baseline<br>and as rule layer (D1,<br>C2)|
|Isolation Forest|Fast, unsupervised,<br>few hyperparameters,<br>works on tabular<br>features (<br>Liu et al.,<br>ICDM 2008)|Axis-aligned splits;<br>weak on correlated<br>high-dim features;<br>scores not<br>probabilities|**Good**first<br>unsupervised<br>detector on<br>engineered features|
|One-Class SVM|Principled one-class<br>boundary|Scales poorly;<br>kernel/νsensitive;<br>poor in high<br>dimension|Moderate; use on<br>low-dim features<br>only|
|Random Forest|Strong on tabular<br>features, robust,<br>cheap, native<br>importances|Needs labels for<br>each attack class;<br>misses unseen<br>attacks|**Good**as attack-type<br>classifier after<br>detection|
|XGBoost / gradient<br>boosting|Usually best tabular<br>accuracy; SHAP-<br>friendly|Same label<br>dependence;<br>overfits small data|Good alternative to<br>RF for the classifier<br>stage|



Page 33 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Method|Advantages|Disadvantages|Suitability for our<br>project|
|---|---|---|---|
|Autoencoder|Normal-only training;<br>reconstruction error<br>localises anomalies;<br>tiny FC version runs<br>on MCU (G1)|Reconstructs some<br>anomalies too well;<br>flags benign<br>novelty|**Strong**for<br>spectrum/PSD<br>branch|
|Variational AE|Likelihood-style score;<br>smoother latent|Not consistently<br>better than AE (A4<br>found Transformer<br>best)|Optional ablation|
|CNN<br>(spectrogram or<br>IQ)|Learns local TF/IQ<br>patterns; proven for<br>jamming typing (B6,<br>D2) and fingerprinting<br>(B2)|Needs labels;<br>adversarially fragile<br>(F1)|Good for supervised<br>attack typing and<br>fingerprint<br>embedding|
|CNN-LSTM|Adds temporal<br>context across frames|More parameters,<br>harder to compress|Use only if single-<br>window CNN fails on<br>intermittent attacks|
|LSTM / GRU|Forecast-residual<br>detection for<br>sequences (B7, E3)|Sequential<br>inference latency;<br>drift|**Strong**for<br>frame/telemetry<br>sequence branch|
|Transformer|Best in A4 and A6|Data- and compute-<br>hungry; hard on<br>edge|Not recommended<br>as core; cite as upper<br>bound|
|Self-supervised /<br>contrastive<br>(Siamese, triplet,<br>few-shot)|Learns from<br>unlabelled or few-<br>label data; supports<br>unseen classes (B1,<br>D3)|Training complexity|**Strong**for<br>replay/spoof<br>fingerprint branch|
|Hybrid ML + rule-<br>based|Combines calibrated<br>rules with learned<br>detectors;<br>explainable; harder to<br>evade wholesale|Fusion must be<br>calibrated; more<br>engineering|**Recommended**<br>**architecture**|



Page 34 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

## 5. Public datasets 

No public dataset contains a **satellite ground link with labelled jamming, spoofing, replay and benign degradation together** . The realistic plan is to combine one real satellite IQ dataset (SatIQ), one real attack IQ dataset (TEXBAT or Fraunhofer), and a synthetic generator (TorchSig) for controlled attacks and degradations. Licences marked "check record" were not confirmed on the landing page during this review. 

### 5.1 Signal-level (IQ / spectrum) datasets 

|Name|Link|Signal /<br>data|IQ or<br>decode<br>d|Samplin<br>g rate|Modula<br>tions|Attack<br>types|Normal<br>samples|Size|Labels|Licence|Downlo<br>ad|Student-<br>feasible|
|---|---|---|---|---|---|---|---|---|---|---|---|---|
|SatIQ<br>Iridium<br>dataset<br>[SAT]|Zenodo<br>822049<br>4 ·<br>code|Iridium<br>ring-<br>alert<br>downlin<br>k<br>headers|IQ +<br>metadat<br>a|25 MS/s|Iridium<br>burst<br>wavefor<br>m|None in<br>data;<br>replay<br>done in<br>paper|1,706,55<br>6<br>messag<br>es|135.4 GB<br>in 35<br>archives<br>of about<br>4 GB<br>(about<br>50,000<br>message<br>s each)|Satellite<br>+<br>transmit<br>ter ID,<br>position,<br>time,<br>noise|CC BY-<br>NC 4.0|Yes|**Yes**—<br>best<br>satellite<br>fit;<br>downloa<br>d 1–2<br>archives|
|PAST-AI<br>Iridium<br>IQ [SAT]|Linked<br>from<br>paper|Iridium|IQ|See<br>paper|Iridium|None<br>(authent<br>ication<br>task)|>100 M<br>IQ<br>samples|Large|Satellite<br>ID|Check<br>record|Per<br>authors|Yes, as a<br>second<br>satellite<br>source|
|TEXBAT<br>[GNSS]|UT<br>Radiona<br>vigation<br>Lab|GPS L1<br>C/A|IQ, 16-<br>bit<br>comple<br>x|25<br>Msps,<br>20 MHz<br>BW|GPS<br>C/A|Time/po<br>sition<br>push<br>spoofing<br>, SCER|Clean<br>static +<br>clean<br>dynami<br>c|Several<br>GB per<br>scenario|Scenario<br>-level|Check<br>page|Yes|Yes —<br>real<br>attack IQ<br>with<br>matched<br>clean<br>data|
|Fraunhof<br>er GNSS<br>Interfere<br>nce<br>Spectru<br>m<br>Highway<br>Dataset 1<br>[GNSS]|IEEE<br>DataPor<br>t ·<br>GitLab|E1 + E6<br>wideban<br>d<br>snapsho<br>ts|Spectral<br>snapsh<br>ots<br>(.npy)|See<br>record|GNSS|8 real<br>interfere<br>nce<br>classes|3 non-<br>interfer<br>ence<br>classes|Moderat<br>e|Class<br>labels +<br>FSL<br>splits|DataPor<br>t terms|Yes<br>(accoun<br>t may be<br>needed)|Yes —<br>real-<br>world<br>jamming<br>with<br>predefin<br>ed splits|
|Morales<br>Ferre<br>jammer<br>dataset<br>[GNSS]|Linked<br>in<br>Sensors<br>paper<br>Sec. 6|Simulate<br>d<br>jammed<br>GNSS|TF<br>images|See<br>paper|GNSS|5<br>jammer<br>types|No-<br>jammer<br>class|Small|6<br>classes|Open<br>access<br>(per<br>paper)|Yes|Yes —<br>quick<br>sanity<br>benchm<br>ark|
|GNSS L1<br>jammed<br>recordin<br>gs<br>[GNSS]|Zenodo<br>14851311<br>·<br>20 MHz|GPS L1<br>under<br>chirp<br>jamming|IQ|10 or 20<br>MHz|GNSS|Linear<br>wide/nar<br>row<br>chirp at<br>several<br>powers|Not in<br>these<br>records|~40–50<br>GB per<br>record|Jammer<br>type +<br>power|Check<br>record|Yes|Partly —<br>very<br>large|
|Tuni2025<br>GPS<br>spoofing<br>[GNSS]|Zenodo<br>1741325<br>8|GPS L1|IQ,<br>float32<br>interlea<br>ved|See<br>record|GPS|1/2/4<br>spoofers<br>, with<br>and<br>without<br>multipat<br>h,<br>delayed<br>injection|Clear-<br>sky<br>authenti<br>c|Large|Scenario|Check<br>record|Yes|Yes —<br>includes<br>**multipat**<br>**h +**<br>**spoofing**<br>, useful<br>for H_D<br>vs H_A|



Page 35 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Name|Link|Signal /<br>data|IQ or<br>decode<br>d|Samplin<br>g rate|Modula<br>tions|Attack<br>types|Normal<br>samples|Size|Labels|Licence|Downlo<br>ad|Student-<br>feasible|
|---|---|---|---|---|---|---|---|---|---|---|---|---|
|GNSS<br>RFF<br>fingerpri<br>nting<br>data<br>[GNSS]|Zenodo<br>10.5281/<br>zenodo.1<br>3846381|GNSS,<br>clean vs<br>spoofed<br>PRNs|IQ|See<br>record|GNSS|Spoofed<br>PRNs|Clean|1,000-<br>sample<br>subsets|Clean /<br>spoofed|CC BY<br>4.0|Yes|Yes|
|RadioML<br>2018.01A<br>[GEN]|DeepSig<br>dataset<br>s|Syntheti<br>c + OTA<br>modulat<br>ed<br>signals|IQ|1024-<br>sample<br>frames|24|None|All|2,555,90<br>4 frames|Modulati<br>on, SNR|CC BY-<br>NC-SA<br>4.0|Yes|Yes —<br>pretraini<br>ng and<br>adversar<br>ial-ML<br>experim<br>ents|
|Sig53 /<br>TorchSig<br>[GEN]|arXiv ·<br>TorchSig<br>repo|Syntheti<br>c<br>narrowb<br>and|IQ|Configu<br>rable|53|None<br>(add<br>your<br>own)|Unlimite<br>d|Generat<br>ed|Class +<br>impairm<br>ents|Open<br>source<br>(check<br>repo)|Yes|**Yes —**<br>**the**<br>**generat**<br>**or for**<br>**controll**<br>**ed**<br>**attacks**<br>**and**<br>**degrada**<br>**tions**|



### 5.2 Frame / protocol / telemetry datasets 

|Name|Link|Data|IQ or<br>decoded|Attack types|Normal|Size|Labels|Licence|Student-<br>feasible|
|---|---|---|---|---|---|---|---|---|---|
|CuCD-ID<br>CubeSat<br>IDS [SAT]<br>[DS]|Mendeley<br>10.17632/7n2<br>d42pm3n.3|NOS3/cFS +<br>COSMOS,<br>CCSDS space<br>packets,<br>header fields +<br>20 s window<br>statistics|Decode<br>d|Command<br>flooding, GPS<br>data injection,<br>limit-<br>checker/check<br>sum tampering,<br>storage<br>exhaustion|Normal<br>operation<br>s|25,000 rows<br>× 31 cols (+<br>22,465<br>noised)|5<br>scenarios|Check<br>record|**Yes**— only<br>public CCSDS-<br>packet IDS<br>data found;<br>simulated,<br>recent, no<br>peer-reviewed<br>paper yet|
|ESA-ADB<br>[SAT]|GitHub ·<br>Kaggle<br>challenge|Real telemetry,<br>2 missions + TC<br>events|Decode<br>d|Faults (no<br>attacks)|Majority|Large|Expert<br>annotatio<br>ns|Check<br>repo|Yes, for data-<br>manipulation<br>injection<br>experiments|
|NASA<br>SMAP / MSL<br>[SAT]|telemanom|Telemetry<br>channels|Decode<br>d|Faults|Majority|Small|Anomaly<br>intervals|Check<br>repo|Yes, but see<br>Wu & Keogh<br>critique|
|STIN [SAT-<br>network]|Used by<br>JNSM 2023|Network flows|Decode<br>d (IP)|Mostly DoS|Yes|Moderate|Attack<br>class|Check<br>source|Not<br>recommended<br>(wrong layer,<br>saturated<br>results)|



### 5.3 Complementing the official challenge dataset 

The right complement depends on what the challenge data contains: 

- **If it provides raw IQ:** pretrain or sanity-check the IQ encoder on RadioML/TorchSig; use SatIQ to test replay/spoof detection on real satellite hardware impairments; use TorchSig impairments to create a benign-degradation set (low SNR, Doppler, fading, CFO drift) so the detector is scored on H_D, not just H_0. 

Page 36 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

- **If it provides decoded frames or packets:** use CuCD-ID for packet-level attack features and generate your own CCSDS-style frame streams with injected replay, counter rollback and gap attacks (deterministic labels). 

- **If it provides telemetry/EO products:** use ESA-ADB to inject data-manipulation attacks into real telemetry and test cross-channel consistency. 

- **In every case:** hold out entire recordings or passes for testing. Fraunhofer's crossdataset results show that random splits overstate performance (Raichur et al.). 

## 6. Three candidate architectures 

**Architecture 3 (hybrid) ranks first overall** ; Architecture 1 is the mandatory baseline you will build anyway, and Architecture 2 becomes one branch of Architecture 3 rather than a standalone system. 

### 6.1 Specifications 

|Stage|Arch. 1 — Classical<br>ML|Arch. 2 — Deep<br>anomaly detector|Arch. 3 — Hybrid<br>security architecture|
|---|---|---|---|
|Input|IQ windows +<br>decoded frame<br>metadata|IQ windows (and PSD<br>frames)|IQ windows + decoded<br>frames + pass<br>geometry (ephemeris)|
|Preprocessing|Normalise power,<br>coarse CFO<br>correction, frame<br>sync|Same + per-window<br>normalisation|Same + compute<br>expected SNR/Doppler<br>for the pass|
|Representation|Hand-crafted<br>statistics per window<br>and per frame|Log-PSD frames /<br>STFT patches; raw IQ<br>for embeddings|Three streams:<br>spectral frames, IQ<br>preamble/header<br>snippets, frame-event<br>sequence|



Page 37 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Stage|Arch. 1 — Classical<br>ML|Arch. 2 — Deep<br>anomaly detector|Arch. 3 — Hybrid<br>security architecture|
|---|---|---|---|
|Feature<br>extraction|Power, noise floor,<br>SNR, spectral<br>flatness, kurtosis,<br>occupied bandwidth,<br>EVM, CFO, IQ<br>imbalance, sync-peak<br>sharpness; frame<br>error rate, counter<br>delta, inter-arrival,<br>timestamp skew|Learned by the<br>network|Engineered physics<br>features (as Arch. 1)<br>**plus residuals vs**<br>**predicted geometry**,<br>learned AE latent,<br>fingerprint embedding|
|Model|Isolation Forest<br>(unsupervised) →<br>Random Forest /<br>XGBoost (attack<br>type)|Small conv/FC<br>autoencoder on PSD<br>(SAIFE/MLPerf style);<br>optional Siamese<br>fingerprint encoder|Rule layer (SDLS-style<br>counter/window<br>checks, power–quality<br>consistency) + AE<br>spectrum detector +<br>fingerprint verifier +<br>GRU/DeepLog-style<br>sequence model +<br>gradient-boosted<br>fusion|
|Anomaly score|IF path length score|Reconstruction error|Calibrated per-branch<br>scores fused into one<br>posterior over<br>{nominal, degraded,<br>attack-k}|
|Decision|Threshold at target<br>FPR on validation<br>passes|Dynamic<br>nonparametric<br>threshold (Hundman)|Two-stage: alarm if<br>fused attack posterior<br>>τ(α); "unknown"<br>(reject) if branches<br>disagree or input is<br>out-of-distribution|
|Explanation|Feature importances<br>/ SHAP|Reconstruction-error<br>heatmap over<br>frequency/time|Which rule fired + top<br>SHAP features of the<br>fusion model + which<br>branch dominated +<br>spectrum heatmap|
|Key literature|D1, D2,<br>Liu et al. 2008,<br>SHAP|A3, A4, G1, B7|B1, C2, D1, E1, E3, F5|



Page 38 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

### 6.2 Ranking 

Scores are 1 (weakest) to 3 (strongest), judged from the literature above, not measured. 

|Criterion|Arch. 1<br>Classical|Arch. 2<br>Deep AD|Arch. 3<br>Hybrid|Rationale|
|---|---|---|---|---|
|Novelty|1|2|3|Fusion of physical, protocol<br>and fingerprint evidence<br>with an explicit degradation<br>class is not found in the<br>literature|
|Feasibility (4–6<br>weeks)|3|2|2|Arch. 3 reuses Arch. 1<br>features and a small AE; cut<br>the fingerprint branch if<br>time runs out|
|Detection<br>performance|2|2|3|Single-view detectors miss<br>attacks invisible in their<br>view (replay is invisible in<br>PSD)|
|False-positive<br>control|2|1|3|One-class DL flags benign<br>novelty; consistency rules<br>and geometry residuals<br>suppress it|
|Robustness<br>(adversarial, shift)|2|1|3|Evading several<br>independent views is<br>harder than one (<br>F2,<br>F5)|
|Computational cost|3|2|2|Arch. 3 core is trees + tiny<br>AE; fingerprint branch is<br>the expensive part|
|Reproducibility|3|2|2|More components = more<br>seeds and configs to<br>publish|
|Suitability for the<br>challenge|2|2|3|Matches "securing the<br>observation chain" end to<br>end, and gives judges<br>explanations|



Page 39 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Criterion|Arch. 1|Arch. 2|Arch. 3|Rationale|
|---|---|---|---|---|
||Classical|Deep AD|Hybrid||
|Suitability for edge|3|2|2|All Arch. 3 branches except<br>the fingerprint encoder fit|
|||||MCU/SBC budgets (G1, G2)|
|**Total (of 27)**|**21**|**16**|**23**||



## 7. The research gap 

The best-supported gap is the combination of two verified gaps: **satellite ground-link detectors do not separate benign degradation from attacks, and none fuse signal-level with protocol-level evidence.** Both were checked against every paper above. 

### 7.1 Candidate gaps, checked against the literature 

|Candidate gap|Verdict|Evidence|
|---|---|---|
|Jamming detectors do not<br>distinguish attacks from<br>natural channel<br>degradation|**Supported for satellite**<br>**links**; partly solved<br>elsewhere|The only TT&C ML jamming<br>detector is binary with no<br>degradation class and reports<br>accuracy only (B6); NGSO<br>interference work is non-<br>adversarial (A4). GNSS (C2) and<br>terrestrial wireless (D1) solve it<br>with statistical consistency tests,<br>not ML|
|Detectors classify but do<br>not explain|**Supported**|Only SAIFE (A3) learns<br>interpretable features; none of<br>the satellite security papers (B1–<br>B6) report explanations|
|Good offline, too heavy for<br>edge|**Partly supported**|Compression is well studied for<br>modulation classifiers (G2, G3)<br>but not for RF anomaly detectors;<br>most detection papers omit<br>latency (Section 4.1)|
|Vulnerable to adversarial<br>examples|**Supported in general,**<br>**untested for this setting**|Shown for RF classifiers (F1–F3);<br>no evaluation of a satellite-link<br>anomaly detector found|



Page 40 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Candidate gap|Verdict|Evidence|
|---|---|---|
|Research focuses on<br>GNSS rather than generic<br>satellite-ground links|**Supported**|Most ML spoofing/jamming work<br>(C3, C4, D2–D4) is GNSS; non-<br>GNSS satellite work is limited to<br>Iridium fingerprinting (B1–B4) and<br>NGSO interference (A4)|
|Systems detect single-<br>layer anomalies, do not<br>combine signal- and<br>protocol-level evidence|**Supported**|Every paper in A–E uses one<br>layer; protocol-level ML on<br>CCSDS frames is absent<br>(Category E)|
|Pass geometry<br>(ephemeris) is not used as<br>side information|**Supported as an**<br>**absence; benefit**<br>**unproven**|No detector found conditions on<br>predicted<br>elevation/Doppler/path loss.<br>FadePrint (B3) uses fading as a<br>fingerprint, which is the closest<br>idea. Whether geometry<br>conditioning reduces false alarms<br>is a hypothesis, not a literature<br>result|



### 7.2 Proposed research question 

**RQ:** For a satellite-to-ground link, does fusing geometry-conditioned physical-layer consistency features with frame/protocol sequence evidence reduce the false-alarm rate under benign channel degradation, at equal detection rate, compared with single-layer ML detectors — while staying robust to an adaptive attacker and within an edge latency budget? 

Testable sub-hypotheses: 

1. **H1 (degradation awareness):** At a fixed true-positive rate of 90%, the fused detector's false-positive rate on a benign-degradation test set (low elevation, fading, CFO drift, noise-floor rise) is lower than that of (a) an energy detector, (b) an IQ/PSD autoencoder alone, (c) a spectrogram CNN trained as in B6. 

2. **H2 (cross-layer coverage):** The fused detector detects replay and sequence attacks that are invisible to spectrum-only detectors, and low-power jamming that is invisible to protocol-only rules. 

3. **H3 (robustness and cost):** Under an adaptive evasion attack constrained to keep the attacker's payload decodable (Flowers et al.'s criterion), the fused detector's detection drops less than the single-branch DL detector; INT8 quantisation keeps per-window 

Page 41 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

#### latency within budget with less than a pre-stated AUC loss. 

**Where the literature is thin.** No paper validates H1 for satellite links, so the expected benefit is an inference from D1 and C2. If the challenge data lacks pass geometry, H1 reduces to SNR-conditioned consistency checks, which still test the core idea. 

## 8. Proposed final detection pipeline 

The pipeline runs four independent evidence branches — rules, spectrum autoencoder, transmitter fingerprint, event-sequence model — and fuses them into a calibrated threeway decision (nominal, degraded, attack-k) with a reject option. Each branch exists because a paper showed it catches something the others cannot. 



<!-- Start of picture text -->
Four independent evidence branches feed one calibrated decision<br>Raw IQ windows Decoded frames Pass geometry (ephemeris)<br>Preprocessing<br>normalise power :CFO and Doppler correction : frame sync: expected SNR and Dopplerfor this pass<br>Rule layer Spectrum AE Fingerprint Sequence model<br>counter window log-PSD frames header IQ frame and command<br>power-FER check reconstruction error Siamese embedding event stream<br>geometry residual CFAR threshold distanceto known Tx next-event surprise<br>Calibrated fusion (gradient boosting, SHAP explanations)<br>P(nominal) - P(degraded) : P(attack: jam, spoof, replay, inject, sequence, timing)<br>below t(a) attack > t(a) reject<br>Nominal or degraded Attack k Unknown<br>log only, no alarm alarm + rule that fired branches disagree or OOD<br>degradation is not an attack top features + PSD heatmap escalate to operator<br><!-- End of picture text -->

proposed detector · 3 inputs, 4 branches, 1 fusion, 3 outcomes 

Only the fusion stage raises alarms, and benign degradation is an explicit outcome rather than a false positive; disagreement between branches escalates instead of guessing. 

Page 42 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

### 8.1 Why each component is there 

|Component|Concrete choice|Literature support|What it catches that<br>others miss|
|---|---|---|---|
|Windowing|Frame-aligned<br>windows; per-window<br>latency = window<br>length + inference|SatIQ and<br>PAST-AI<br>operate on message<br>headers; PAST-AI<br>warns about sample<br>budget|Bounds detection<br>delay|
|Geometry<br>residuals|Measured minus<br>predicted SNR and<br>Doppler for the pass|Consistency principle<br>of<br>Xu et al. and<br>Wesson et al.;<br>geometry use itself is<br>our hypothesis<br>(Section 7)|Separates fades at<br>low elevation from<br>jamming; flags<br>terrestrial spoofers|
|Rule layer|Anti-replay window,<br>timestamp skew,<br>power–FER<br>consistency|SDLS / SPACE-<br>SHIELD M2004;<br>Xu et<br>al.|Exact, explainable<br>replay and sequence<br>detection with zero<br>training|
|Spectrum<br>autoencoder|Small FC/conv AE on<br>log-PSD frames, CFAR-<br>calibrated|SAIFE;<br>MLPerf Tiny<br>AD;<br>Saifaldawla et al.<br>show learned models<br>beat energy<br>detection|Unseen jammers and<br>interference|
|Fingerprint<br>verifier|SatIQ-style Siamese<br>embedding of header<br>IQ; distance to enrolled<br>transmitters|SatIQ;<br>PAST-AI;<br>jamming resilience in<br>Sticky Fingers|Replay and spoofing<br>with valid content|
|Sequence<br>model|Start with n-gram /<br>Markov on (frame type,<br>APID, counter delta);<br>GRU only if needed|DeepLog;<br>Telemanom|Out-of-grammar<br>command sequences,<br>flooding, reordering|
|Fusion|Gradient-boosted<br>model on branch<br>scores + key physical<br>features, isotonic<br>calibration|Classical ML<br>competitive on RF<br>features (<br>Morales<br>Ferre et al.);<br>calibrated tabular<br>models explainable<br>with<br>SHAP|Combines weak<br>signals; learns which<br>branch to trust per<br>condition|



Page 43 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Component|Concrete choice|Literature support|What it catches that<br>others miss|
|---|---|---|---|
|Reject option|Abstain when branches<br>disagree or input is out-<br>of-distribution|Run-time rejection in<br>Zhang et al.|Limits damage from<br>adversarial evasion of<br>any single branch|
|Thresholding|Fixed FPR budgetαon<br>held-out benign<br>passes; dynamic<br>threshold + pruning on<br>score streams|Telemanom;base-rate<br>argument of<br>Axelsson|Keeps alarm volume<br>operator-tolerable|
|Explanation|Rule fired + top SHAP<br>features + PSD<br>reconstruction<br>heatmap + dominant<br>branch|SAIFE;<br>SHAP|Gives judges and<br>operators a reason,<br>not just a score|
|Response|Log, alarm, or escalate;<br>no automatic frame<br>blocking on DL score<br>alone|DoS-via-detector risk<br>measured in<br>Sticky<br>Fingers|Avoids turning the<br>detector into a<br>denial-of-service tool|



### 8.2 Evaluation protocol 

- **Splits:** by pass or recording, never random frames (Arp et al.). 

- **Test sets:** clean, benign-degraded, each attack type, and unseen attack variants held out of training. 

- **Metrics:** TPR at FPR of 1% and 0.1%, FPR on the degraded set specifically, EER, perclass F1, detection delay, per-window latency, model size before and after INT8. 

- **Baselines run on the same splits:** energy detector; Gecgel-style STFT CNN; Architecture 1; spectrum AE alone. 

- **Ablations:** remove each branch and the geometry residuals one at a time; this directly tests H1 and H2. 

- **Adversarial:** PGD on the DL branches under a payload-must-decode constraint; jamming-induced false-reject curve. 

## 9. Attacker versus defender 

Every attack in the table can be reproduced in software by manipulating recorded or synthetic IQ and frame streams; the realism limits are listed after the table. 

Page 44 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

|Attack|Attacker capability|Observable effect|Detection<br>features|ML method|Countermeasure|
|---|---|---|---|---|---|
|Barrage / tone /<br>chirp jamming|Transmitter in band;<br>no protocol<br>knowledge|Noise floor or<br>narrowband power<br>rise; PSD shape<br>change; frame errors|Noise floor,<br>spectral<br>flatness,<br>kurtosis, PSD<br>residual, power–<br>FER<br>consistency|Energy<br>detector<br>baseline; PSD<br>autoencoder<br>(A3, G1);<br>RF/XGBoost<br>for type (D2)|Alarm + type;<br>operator<br>reschedules pass<br>or switches<br>frequency|
|Intermittent /<br>protocol-aware<br>jamming|Knows frame timing;<br>jams headers or<br>specific telemetry<br>fields|Errors clustered at<br>fixed offsets; little<br>average power<br>change|Per-field error<br>position<br>histogram, burst<br>periodicity,<br>STFT patches|STFT CNN<br>(B6); sequence<br>model on error<br>positions|Alarm;<br>interleaving/FEC<br>change at protocol<br>level|
|Spoofing<br>(overshadowing)|Ground SDR, higher<br>power in main lobe or<br>sidelobe; can<br>generate valid<br>frames|Power step, AGC<br>change, Doppler not<br>matching ephemeris,<br>different hardware<br>impairments|Power residual<br>vs geometry,<br>Doppler<br>residual,<br>CFO/IQ-<br>imbalance,<br>fingerprint<br>distance|Fingerprint<br>embedding<br>(B1, B2); IF on<br>residuals|Reject frames;<br>require SDLS<br>authentication|
|Replay|Records genuine IQ<br>or frames,<br>retransmits later|Valid CRC; stale<br>counter/timestamp;<br>replaying device's<br>impairments|Counter vs<br>window,<br>timestamp skew<br>vs reception<br>time, fingerprint<br>distance|Rule layer (E1)<br>+ Siamese<br>verifier (B1)|Anti-replay<br>window; drop +<br>alert|
|Data manipulation<br>(injection, false<br>telemetry)|Can craft valid<br>frames (no auth) or<br>compromise source|Values physically<br>inconsistent; broken<br>cross-channel<br>correlation|Forecast<br>residuals per<br>channel, cross-<br>channel<br>consistency|LSTM<br>forecaster +<br>dynamic<br>threshold (B7);<br>IF|Quarantine data<br>products; flag to<br>EO users|
|Sequence<br>manipulation<br>(gaps, reorder,<br>rollback, flooding)|Inject/drop/reorder<br>frames|Counter gaps,<br>regressions,<br>duplicate IDs, rate<br>spikes|Counter delta,<br>inter-arrival<br>time, event n-<br>grams|Rules +<br>DeepLog-style<br>next-event<br>model (E3)|Reject out-of-<br>window frames;<br>rate limit|
|Timing attacks<br>(delay, time-sync<br>manipulation)|Delays forwarding or<br>alters timestamps|Latency/jitter<br>change; timestamp vs<br>local clock drift|Inter-arrival<br>distribution,<br>clock-offset<br>residual,<br>timestamp<br>monotonicity|CUSUM /<br>change-point;<br>LSTM residual|Trusted local time;<br>alert on drift|
|Adversarial<br>evasion of the<br>detector|Knows or<br>approximates the<br>model; adds crafted<br>perturbation|Small, structured IQ<br>change that lowers<br>the attack score|Disagreement<br>between<br>branches, OOD<br>score, input-<br>gradient norm|Adversarial<br>training (F6),<br>rejection (F5),<br>multi-view<br>fusion|Escalate<br>"unknown"; never<br>auto-trust a single<br>DL score|
|DoS via the<br>detector (induced<br>false rejects)|Low-power jamming<br>targeted at the<br>fingerprint|Legitimate frames<br>rejected|Rejection rate<br>vs SNR|Measure as in<br>B4|Fail-open policy for<br>availability-critical<br>links|



Page 45 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

### 9.1 Realistic to reproduce in a controlled software testbed 

|Attack|Reproducible in<br>software?|How|Realism caveat|
|---|---|---|---|
|Jamming (all|Yes|Add synthetic jammer|No AGC saturation or|
|types)||waveforms to<br>recorded/synthetic IQ at<br>controlled JSR|front-end nonlinearity<br>unless modelled|
|Spoofing|Partly|Overlay a second<br>transmitter with different<br>CFO, IQ imbalance, phase<br>noise (TorchSig<br>impairments) and a power<br>step|Real hardware<br>fingerprints are richer<br>than simulated<br>impairments; validate<br>on SatIQ data|
|Replay|Yes at frame level;<br>partly at IQ level|Re-insert recorded frames<br>later; at IQ level add a<br>replay-chain impairment<br>model|Simulated replay is<br>easier to detect than<br>real SDR replay (B1<br>used real SDR)|
|Data<br>manipulation|Yes|Inject offsets, drifts,<br>freezes into telemetry<br>fields|Attack realism<br>depends on mission<br>knowledge|
|Sequence /<br>timing|Yes|Manipulate counters,<br>timestamps, inter-arrival in<br>decoded streams|Deterministic; labels<br>exact|
|Adversarial<br>evasion|Yes (white-box,<br>input access)|FGSM/PGD on IQ inputs;<br>check payload still<br>decodes (F2 criterion)|Over-the-air effects<br>not modelled;<br>overstates attacker|



## 10. Final recommendation 

Build the Section 8 hybrid detector, lead with the degradation-vs-attack false-alarm result (H1), and treat the fingerprint branch as the stretch goal. 

### A. Top 10 papers to read 

1. Smailes et al., Watch This Space (SatIQ), CCS 2023 — satellite replay/spoof detection with code and data 

2. Oligeri et al., PAST-AI, IEEE TIFS 2023 — independent satellite fingerprinting and its latency limits 

Page 46 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

3. Gecgel & Kurt, Intermittent Jamming against TT&C, WiseML 2021 — the TT&C baseline to beat 

4. Rajendran et al., SAIFE, IEEE TCCN 2019 — explainable, CFAR-calibrated spectrum AE 

5. Wesson et al., Power and Distortion Monitoring — evaluating against multipath and jamming, not just clean data 

6. Xu et al., Feasibility of Launching and Detecting Jamming, MobiHoc 2005 — consistency checks for degradation vs attack 

7. Hundman et al., Telemanom, KDD 2018 — dynamic thresholds and false-positive pruning 

8. Flowers et al., Evaluating Adversarial Evasion in Wireless, IEEE TIFS 2020 — the right adversarial threat model 

9. Salkield et al., Satellite Spoofing from A to Z, WiSec 2023 — realistic satellite spoofer 

10. Arp et al., Dos and Don'ts of Machine Learning in Computer Security, USENIX Security <u>2022</u> — the evaluation pitfalls judges will check 

### B. Top 5 most directly applicable 

1. **SatIQ** — reuse weights/code for the replay/spoof branch 

2. **Gecgel & Kurt** — reproduce as the supervised CNN baseline and show where it fails on degradation 

3. **SAIFE** — template for the PSD autoencoder branch with CFAR thresholds 

4. **Xu et al.** — power–quality consistency rule 

5. **Hundman et al.** — threshold and pruning logic for every score stream 

### C. Top 3 datasets 

1. SatIQ Iridium dataset — real satellite IQ at 25 MS/s with transmitter labels 

2. TEXBAT — real spoofing IQ with matched clean recordings (GNSS, for cross-domain validation) 

3. TorchSig / Sig53 — generator for controlled jamming, spoofing impairments and benign degradation 

All three complement, not replace, the official challenge data. 

### D. Top 3 ML approaches 

1. **Isolation Forest + gradient-boosted classifier on physics and protocol features, explained with SHAP** — cheap, explainable, strong baseline 

2. **Small PSD autoencoder with dynamic thresholding** — unsupervised coverage of unseen jamming/interference 

Page 47 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

3. **Siamese/triplet fingerprint embedding** — the only verified route to physical-layer replay/spoof detection without cryptography 

### E. The most promising research gap 

Degradation-aware, cross-layer fusion: no satellite ground-link detector separates benign degradation from attacks, and none fuse signal-level and protocol-level evidence (Section 7, RQ and H1–H3). 

### F. Proposed final architecture 

The four-branch hybrid in Section 8: rule layer + PSD autoencoder + fingerprint verifier + sequence model, fused by a calibrated gradient-boosted model with a reject option and SHAP explanations. 

### G. Roadmap for the next 6 weeks 

1. **Week 1 — Data and evaluation harness.** Audit the official data format. Split by pass/recording, never randomly. Build the benign-degradation set (low elevation, fading, CFO drift, noise rise) and the attack injectors (Section 9.1). Fix the metric suite: TPR at FPR = 1% and 0.1%, ROC/PR AUC, EER, detection delay (windows to alarm), perwindow latency, model size. 

2. **Week 2 — Baselines.** Energy detector, rule layer (counter window, timestamp skew, power–FER consistency), Architecture 1 (Isolation Forest → XGBoost) with SHAP. Reproduce a Gecgel-style STFT CNN as the DL baseline. 

3. **Week 3 — Spectrum branch.** PSD autoencoder (MLPerf Tiny-sized) with Hundmanstyle dynamic thresholds. First H1 result: FPR on degradation set at fixed TPR, vs baselines. 

4. **Week 4 — Protocol branch and fusion.** Sequence model (start with n-gram/Markov, then a small GRU if needed); calibrated fusion with reject option; explanation output per alarm. 

5. **Week 5 — Robustness.** PGD evasion on the DL branches with a payload-must-decode constraint; jamming-induced false-reject test (Sticky Fingers protocol). Stretch: fingerprint branch using SatIQ pretrained weights on a subset. 

6. **Week 6 — Edge and write-up.** INT8 post-training quantisation; CPU latency per window; ablation table (each branch removed); release code, seeds, configs and split files. 

### H. What not to implement 

- **A Transformer core.** Best accuracy in A4 and A6, but unjustified compute for an edge detector and no FPR evidence. 

Page 48 of 49 

SOTA Review: AI Attack Detection for a Secure Satellite Ground-Link Testbed (TSYP14 Track 1) 

- **Training a fingerprinting model from scratch on the full 1.7 M-message SatIQ set.** Use the published weights or a subset. 

- **GAN-generated attacks, federated learning, RL anti-jamming.** Each is a project of its own and adds no evidence for the RQ. 

- **IP-flow IDS on STIN / NSL-KDD.** Wrong layer; results are saturated. 

- **Online self-retraining.** Opens a poisoning surface (F4) you cannot evaluate in six weeks. 

- **Accuracy-only reporting or random splits.** Judges familiar with Arp et al. will discount them. 

- **Claims of beating published SOTA** on numbers from papers using different data. Compare only against baselines you run on the same splits. 

- **Hardware work.** Out of scope for this phase, as requested. 

## Sources 

All links were opened or matched to publisher, DOI, arXiv, institutional or dataset records during this review; links appear inline beside each claim. Key records: SatIQ (arXiv), <u>SatIQ dataset, PAST-AI (IRIS), FadePrint, Sticky Fingers, Satellite Spoofing A–Z, Gecgel & Kurt, GenAI NGSO interference (IEEE), SAIFE (IEEE), Tandiya et al., O'Shea et al. 2016, Telemanom, ESA-ADB, Wesson et al., Semanjski et al., Morales Ferre et al., Ott et al., Raichur et al. (Fraunhofer), Xu et al., SPACE-SHIELD M2004, Sadeghi & Larsson, Flowers et al., Adesina et al., Zhang et al., MLPerf Tiny, RFNet, Sig53/TorchSig, CuCD-ID, Fraunhofer Highway dataset. Seminal references cited from standard bibliographic records without</u> re-opening: Axelsson 2000, Sommer & Paxson 2010, Arp et al. 2022, Psiaki & Humphreys 2016, DeepLog 2017, Mo & Sinopoli 2009, Madry et al. 2018, Jacob et al. 2018, Hinton et al. 2015, Liu et al. 2008, Lundberg & Lee 2017, Wu & Keogh, Pavur & Martinovic, O'Shea et al. 2018. 

Page 49 of 49 

