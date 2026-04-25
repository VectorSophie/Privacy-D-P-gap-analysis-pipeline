# 연구 방법론 (Methodology)

본 연구는 국내 스타트업 웹사이트를 대상으로 개인정보 처리방침의 법적 컴플라이언스 준수 여부 및 실제 웹사이트 동작 간의 불일치(Disclosure-Practice Gap)를 자동화된 파이프라인으로 정량 분석합니다. 전체 파이프라인은 8단계로 구성됩니다.

## 1. 기업 데이터 수집 (collect-companies)

중소벤처기업부 벤처기업 명단(MSME, 2025년 2월 기준)과 수동 큐레이션된 국내 주요 스타트업 117개사를 통합합니다. URL이 없는 기업에 대해서는 Naver 검색 API를 통해 공식 홈페이지를 자동 탐색합니다.

중복 제거는 두 단계로 수행합니다.
1. **URL 기반**: 정규화된 도메인이 동일한 경우 (www 제거, 후행 슬래시 무시)
2. **이름 유사도 기반**: 법인 접미사(주식회사, Corp., Ltd. 등) 제거 후 SequenceMatcher 유사도 ≥ 0.88인 경우

최종 분석 모집단: **1,000개사** (URL 확보 957개).

## 2. 처리방침 탐색 및 크롤링 (crawl-sites)

동적 렌더링을 지원하는 Playwright 비동기 API로 각 기업 루트 도메인에서 처리방침 페이지를 자동 탐색합니다. 페이지 로딩은 `networkidle` 조건까지 대기합니다.

탐색 전략은 다음과 같습니다.
- 앵커 텍스트(`개인정보`, `처리방침`, `privacy`, `policy` 등) 및 URL 경로 휴리스틱 매칭
- 1차 탐색 실패 시 BFS 깊이 2까지 순회
- 윤리적 크롤링: robots.txt 준수, 요청 간격 2,000ms, 최대 3회 재시도

크롤링 성공률: **526/957 = 54.9%**. 실패 원인은 robots.txt 차단, 타임아웃, 처리방침 페이지 미탐지, HTTP 오류 등입니다.

**생존 편향 주의**: 크롤링에 성공한 N=526 표본은 처리방침이 접근 가능한 기업에 편향될 수 있습니다. 크롤링 실패 431개사를 모두 under-disclosure로 가정하면 상한 추정치 86.3%가 도출됩니다. 실패 사유 분류는 `scripts/classify_crawl_failures.py`로 산출할 수 있습니다.

## 3. 텍스트 추출 및 전처리 (extract-policies)

수집된 HTML에서 순수 처리방침 텍스트를 추출합니다.

1. `<nav>`, `<footer>`, `<header>`, `<aside>`, `<script>`, `<style>` 등 제거
2. `<main>` 태그를 1순위로 탐색, 없으면 `class`/`id`에 식별 키워드(`content`, `privacy`, `policy`)가 포함된 `<div>` 중 텍스트 길이가 가장 긴 요소를 본문으로 추정
3. 블록 레벨 렌더링 규칙을 모방하여 문단 단위 segmentation

**품질 필터링**: 추출 결과가 500자 미만이거나 `개인정보`, `수집`, `처리방침`, `privacy`, `policy` 키워드를 하나도 포함하지 않는 경우 `quality_flag: low_quality`로 표시됩니다. 이 항목은 LLM 평가 단계에서 자동 제외되며, `scripts/check_extraction_quality.py`로 목록을 확인할 수 있습니다.

## 4. 서드파티 트래커 탐지 (detect-trackers)

Playwright의 `page.on("request")` 이벤트 훅으로 페이지 로드 중 발생하는 모든 네트워크 요청을 캡처합니다.

**필터링**: 정적 리소스(이미지·폰트·스타일시트) 및 범용 CDN(`fonts.googleapis.com` 등) 제외 → First-party 도메인과 루트 도메인이 다른 요청만 서드파티로 분류.

**분류 체계 (6개 카테고리)**:

| 카테고리 | 대표 도메인 |
|---|---|
| Analytics | googletagmanager.com, google-analytics.com, wcs.naver.com, clarity.ms, amplitude.com |
| Advertising | doubleclick.net, googlesyndication.com, nam.veta.naver.com, connect.facebook.net |
| Session Replay | hotjar.com, fullstory.com, smartlook.com |
| Social | facebook.com/tr, platform.twitter.com, snap.licdn.com |
| Fingerprinting | fingerprintjs.com, threatmetrix.com |
| Unknown Third-party | 상기 외 모든 서드파티 요청 |

**분석 결과 (N=526)**: 총 5,334건 탐지. Analytics 33.2%, Advertising 21.7%, Unknown 44.3%, Session Replay 0.3%, Social 0.5%.

## 5. LLM 기반 컴플라이언스 평가 (evaluate-llm)

GPT-4 Turbo (`gpt-4-turbo`, temperature=0.0)로 처리방침 텍스트를 분석합니다. Pydantic v2 스키마로 JSON 출력 형식을 강제하며, 입력 텍스트는 최대 6,000자로 절단합니다.

**평가 항목 (`ComplianceEvaluation` 스키마)**:

| 필드 | 설명 |
|---|---|
| `has_mandatory_items` | 수집 목적·항목·기간 등 법적 필수 항목 명시 여부 |
| `ambiguity_detected` | "필요 시", "관련 법령에 따라" 등 모호한 표현 존재 여부 |
| `legal_omission_detected` | 보호책임자·파기 절차 등 법적 고지 의무 누락 여부 |
| `mentions_third_party_trackers` | 서드파티 트래커·쿠키·애널리틱스 명시적 언급 여부 |

모든 판단에 원문 발췌(evidence span)를 첨부하도록 설계하여 환각(Hallucination) 억제 및 사후 검증을 지원합니다.

**분석 결과 (N=526)**: has_mandatory_items 54.9%, legal_omission 66.9%, ambiguity 3.0%, mentions_third_party_trackers 13.7%.

Mock 모드(`use_mock: true`)에서는 키워드 기반 `MockPolicyEvaluator`로 API 비용 없이 파이프라인을 테스트할 수 있습니다.

## 6. Disclosure-Practice Gap 산출 (compute-mismatch)

트래커 탐지 결과(Practice)와 LLM 평가의 `mentions_third_party_trackers`(Disclosure)를 기업 단위로 비교합니다.

| 레이블 | 정의 | 비율 |
|---|---|---|
| **under_disclosure** | 트래커 운영 + 처리방침 미기재 | **75.1%** (395/526) |
| none | 일치 (둘 다 없거나 둘 다 있음) | 23.6% (124/526) |
| over_disclosure | 처리방침 기재 + 트래커 없음 | 1.3% (7/526) |

## 7–8. 통계 분석 및 보고서 생성 (run-stats / build-report)

`scipy.stats`와 `statsmodels`를 활용하여 카이제곱 검정, Fisher's exact test, Mann-Whitney U 검정, 로지스틱 회귀를 수행합니다. 결과는 CSV 테이블(`outputs/tables/`)과 matplotlib 시각화(`outputs/figures/`)로 출력됩니다.
