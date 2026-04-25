import asyncio
import json
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import typer
import yaml

from src.extraction.extractor import PolicyExtractor

app = typer.Typer(help="국내 스타트업 개인정보 처리방침 Disclosure-Practice Gap 분석 시스템")


def load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


# ── 1. 기업 수집 ──────────────────────────────────────────────────────────────

@app.command()
def collect_companies(config: str = typer.Option(..., help="설정 파일 경로")):
    """1. 스타트업 목록 수집 (멀티소스 + 중복 제거)"""
    typer.echo("[1/8] 후보 기업 수집")
    cfg = load_config(config)
    out_dir = Path(cfg["paths"]["data_raw"])
    ensure_dir(out_dir)

    from src.collection import build_collector, deduplicate

    collector, threshold = build_collector(cfg)
    raw = collector.collect_all()
    typer.echo(f"  수집 원본: {len(raw)}개")

    deduped = deduplicate(raw, name_threshold=threshold)
    typer.echo(f"  중복 제거 후: {len(deduped)}개")

    target_n = int(cfg.get("collection", {}).get("target_n", 120))
    sample = deduped[:target_n]
    typer.echo(f"  최종 표본: {len(sample)}개 (목표 {target_n}개)")

    df = pd.DataFrame([r.to_row() for r in sample])
    out_path = out_dir / "companies.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    typer.echo(f"  -> 저장 완료 ({out_path})")


# ── 1.5. URL 탐색 ─────────────────────────────────────────────────────────────

@app.command()
def discover_urls(
    config: str = typer.Option(..., help="설정 파일 경로"),
    limit: int = typer.Option(0, help="탐색 기업 수 제한 (0=전체)"),
    dry_run: bool = typer.Option(False, help="API 미호출, 대상 기업만 출력"),
):
    """1.5. Naver 검색으로 URL 미확인 기업 홈페이지 자동 탐색"""
    import subprocess, sys
    args = ["uv", "run", "--env-file", ".env", "python", "scripts/discover_urls.py",
            "--config", config]
    if limit:
        args += ["--limit", str(limit)]
    if dry_run:
        args += ["--dry-run"]
    subprocess.run(args, check=False)


# ── 2. 크롤링 ─────────────────────────────────────────────────────────────────

@app.command()
def crawl_sites(
    config: str = typer.Option(..., help="설정 파일 경로"),
    sample: int = typer.Option(0, help="테스트용 샘플 수 (0=전체)"),
    concurrency: int = typer.Option(3, help="동시 크롤링 수"),
):
    """2. 개인정보 처리방침 페이지 크롤링 (Playwright BFS)"""
    typer.echo("[2/8] 크롤링 (Playwright)")
    cfg = load_config(config)
    raw_dir = Path(cfg["paths"]["data_raw"])
    html_dir = raw_dir / "html"
    interim_dir = Path(cfg["paths"]["data_interim"])
    logs_dir = Path(cfg["paths"]["logs"])
    for d in [html_dir, interim_dir, logs_dir]:
        ensure_dir(d)

    all_companies = pd.read_csv(raw_dir / "companies.csv", encoding="utf-8-sig")
    companies = all_companies[
        all_companies["url"].notna() & (all_companies["url"].astype(str).str.strip() != "")
    ].reset_index(drop=True)

    skipped = len(all_companies) - len(companies)
    if skipped:
        typer.echo(f"  URL 미확인 {skipped}개 건너뜀")
    if sample > 0:
        companies = companies.head(sample)
        typer.echo(f"  [샘플 모드] {len(companies)}개")
    typer.echo(f"  크롤링 대상: {len(companies)}개")

    from src.crawling.crawler import PolicyCrawler

    async def _run():
        crawler = PolicyCrawler(cfg)
        sem = asyncio.Semaphore(concurrency)
        total = len(companies)
        done = {"n": 0, "ok": 0}

        async def _one(cid, url):
            async with sem:
                res = await crawler.crawl_company(cid, url, html_dir)
                done["n"] += 1
                if res["status"] == "success":
                    done["ok"] += 1
                typer.echo(f"  [{done['n']}/{total}] {cid}: {res['status']}")
                return res

        tasks = [_one(row["company_id"], row["url"]) for _, row in companies.iterrows()]
        return await asyncio.gather(*tasks, return_exceptions=True)

    raw_results = asyncio.run(_run())
    results = [r for r in raw_results if isinstance(r, dict)]

    crawl_map = {
        r["cid"]: {"status": r["status"], "policy_url": r.get("url")}
        for r in results
    }
    with open(interim_dir / "crawl_results.json", "w", encoding="utf-8") as f:
        json.dump(crawl_map, f, ensure_ascii=False, indent=2)

    ok = sum(1 for r in results if r["status"] == "success")
    typer.echo(f"  -> 성공 {ok}/{len(results)}개 | crawl_results.json 저장")


# ── 3. 정책 추출 ──────────────────────────────────────────────────────────────

@app.command()
def extract_policies(config: str = typer.Option(..., help="설정 파일 경로")):
    """3. HTML에서 개인정보 처리방침 텍스트 추출 (BeautifulSoup)"""
    typer.echo("[3/8] 정책 텍스트 추출")
    cfg = load_config(config)
    raw_dir = Path(cfg["paths"]["data_raw"])
    interim_dir = Path(cfg["paths"]["data_interim"])
    ensure_dir(interim_dir)

    html_dir = raw_dir / "html"
    policies = {}
    low_quality_count = 0
    for html_file in sorted(html_dir.glob("*_policy.html")):
        cid = html_file.stem.replace("_policy", "")
        with open(html_file, "r", encoding="utf-8") as f:
            raw_html = f.read()
        result = PolicyExtractor(raw_html).extract_with_quality()
        policies[cid] = result
        if result["quality_flag"] == "low_quality":
            low_quality_count += 1

    with open(interim_dir / "policies.json", "w", encoding="utf-8") as f:
        json.dump(policies, f, ensure_ascii=False, indent=2)
    typer.echo(f"  -> {len(policies)}개 정책 텍스트 저장 완료")
    if low_quality_count:
        typer.echo(f"  ⚠ low_quality 항목: {low_quality_count}개 (LLM 평가에서 제외됨)")


# ── 4. 트래커 탐지 ────────────────────────────────────────────────────────────

@app.command()
def detect_trackers(
    config: str = typer.Option(..., help="설정 파일 경로"),
    concurrency: int = typer.Option(3, help="동시 탐지 수"),
):
    """4. 기업 홈페이지 트래커 탐지 (Playwright 네트워크 인터셉트)"""
    typer.echo("[4/8] 트래커 탐지 (Playwright)")
    cfg = load_config(config)
    raw_dir = Path(cfg["paths"]["data_raw"])
    interim_dir = Path(cfg["paths"]["data_interim"])
    ensure_dir(interim_dir)

    companies = pd.read_csv(raw_dir / "companies.csv", encoding="utf-8-sig")

    # 크롤링 성공 기업만 대상으로 한정
    crawl_path = interim_dir / "crawl_results.json"
    if crawl_path.exists():
        with open(crawl_path, encoding="utf-8") as f:
            crawl_map = json.load(f)
        ok_cids = {cid for cid, d in crawl_map.items() if d["status"] == "success"}
        companies = companies[companies["company_id"].isin(ok_cids)]

    companies = companies[
        companies["url"].notna() & (companies["url"].astype(str).str.strip() != "")
    ].reset_index(drop=True)
    typer.echo(f"  탐지 대상: {len(companies)}개")

    from src.tracking.tracker import TrackerDetector

    async def _run():
        sem = asyncio.Semaphore(concurrency)
        total = len(companies)
        done = {"n": 0}
        results = {}

        async def _one(cid, url):
            async with sem:
                detector = TrackerDetector(url)
                trackers = await detector.detect_from_url(url)
                done["n"] += 1
                typer.echo(f"  [{done['n']}/{total}] {cid}: {len(trackers)} trackers")
                return cid, trackers

        pairs = await asyncio.gather(
            *[_one(row["company_id"], row["url"]) for _, row in companies.iterrows()],
            return_exceptions=True,
        )
        for item in pairs:
            if isinstance(item, tuple):
                results[item[0]] = item[1]
        return results

    trackers = asyncio.run(_run())
    with open(interim_dir / "trackers.json", "w", encoding="utf-8") as f:
        json.dump(trackers, f, ensure_ascii=False, indent=2)
    typer.echo(f"  -> {len(trackers)}개 기업 트래커 탐지 완료")


# ── 5. LLM 평가 ───────────────────────────────────────────────────────────────

@app.command()
def evaluate_llm(config: str = typer.Option(..., help="설정 파일 경로")):
    """5. 처리방침 LLM 평가 (Mock 또는 OpenAI)"""
    typer.echo("[5/8] LLM 평가")
    cfg = load_config(config)
    interim_dir = Path(cfg["paths"]["data_interim"])
    processed_dir = Path(cfg["paths"]["data_processed"])
    ensure_dir(processed_dir)

    with open(interim_dir / "policies.json", encoding="utf-8") as f:
        policies = json.load(f)

    llm_cfg = cfg.get("llm", {})
    use_mock = llm_cfg.get("use_mock", True)

    from src.llm.evaluator import get_evaluator
    evaluator = get_evaluator(
        use_mock=use_mock,
        model=llm_cfg.get("model", "gpt-4-turbo"),
        temperature=float(llm_cfg.get("temperature", 0.0)),
    )
    mode = "MockEvaluator" if use_mock else f"OpenAI {llm_cfg.get('model', 'gpt-4-turbo')}"
    typer.echo(f"  평가 모드: {mode} | 대상: {len(policies)}개")

    eval_results = {}
    skipped_low_quality = 0
    for i, (cid, policy_data) in enumerate(policies.items(), 1):
        # support both old list format and new dict format from extract_with_quality()
        if isinstance(policy_data, dict):
            if policy_data.get("quality_flag") == "low_quality":
                eval_results[cid] = {
                    "skipped": True,
                    "quality_flag": "low_quality",
                    "quality_reason": policy_data.get("quality_reason", ""),
                }
                skipped_low_quality += 1
                continue
            text = policy_data.get("text") or "\n".join(policy_data.get("paragraphs", []))
        else:
            text = "\n".join(policy_data) if isinstance(policy_data, list) else str(policy_data)
        try:
            result = evaluator.evaluate(text)
            eval_results[cid] = result.model_dump()
        except Exception as e:
            eval_results[cid] = {"error": str(e)}
        if i % 50 == 0:
            typer.echo(f"  진행: {i}/{len(policies)}")

    if skipped_low_quality:
        typer.echo(f"  ⚠ low_quality로 LLM 평가 건너뜀: {skipped_low_quality}개")

    with open(processed_dir / "llm_eval.json", "w", encoding="utf-8") as f:
        json.dump(eval_results, f, ensure_ascii=False, indent=2)
    typer.echo(f"  -> {len(eval_results)}개 평가 완료")


# ── 6. Mismatch 계산 ──────────────────────────────────────────────────────────

@app.command()
def compute_mismatch(config: str = typer.Option(..., help="설정 파일 경로")):
    """6. Disclosure-Practice Mismatch 계산"""
    typer.echo("[6/8] Mismatch 계산")
    cfg = load_config(config)
    interim_dir = Path(cfg["paths"]["data_interim"])
    processed_dir = Path(cfg["paths"]["data_processed"])
    ensure_dir(processed_dir)

    with open(interim_dir / "trackers.json", encoding="utf-8") as f:
        trackers = json.load(f)
    with open(processed_dir / "llm_eval.json", encoding="utf-8") as f:
        eval_results = json.load(f)

    mismatch_data = []
    for cid, t_list in trackers.items():
        has_tracker = len(t_list) > 0
        policy_disclosed = eval_results.get(cid, {}).get("mentions_third_party_trackers", False)

        under_disclosure = has_tracker and not policy_disclosed
        over_disclosure = not has_tracker and policy_disclosed

        label = "none"
        if under_disclosure and over_disclosure:
            label = "composite_mismatch"
        elif under_disclosure:
            label = "under_disclosure"
        elif over_disclosure:
            label = "over_disclosure"

        mismatch_data.append({
            "company_id": cid,
            "has_tracker": has_tracker,
            "tracker_count": len(t_list),
            "policy_disclosed": policy_disclosed,
            "under_disclosure": under_disclosure,
            "over_disclosure": over_disclosure,
            "mismatch_label": label,
        })

    df = pd.DataFrame(mismatch_data)
    df.to_csv(processed_dir / "mismatch.csv", index=False, encoding="utf-8-sig")
    typer.echo(f"  under_disclosure: {df['under_disclosure'].sum()}")
    typer.echo(f"  over_disclosure : {df['over_disclosure'].sum()}")
    typer.echo(f"  -> mismatch.csv 저장 완료 ({len(df)}개)")


# ── 7. 통계 분석 ──────────────────────────────────────────────────────────────

@app.command()
def run_stats(config: str = typer.Option(..., help="설정 파일 경로")):
    """7. 통계 분석"""
    typer.echo("[7/8] 통계 분석")
    cfg = load_config(config)
    processed_dir = Path(cfg["paths"]["data_processed"])
    tables_dir = Path(cfg["paths"]["tables"])
    ensure_dir(tables_dir)

    df = pd.read_csv(processed_dir / "mismatch.csv")
    summary = df[["under_disclosure", "over_disclosure"]].sum().rename("count").reset_index()
    summary.columns = ["type", "count"]
    summary.to_csv(tables_dir / "mismatch_stats.csv", index=False, encoding="utf-8-sig")

    label_dist = df["mismatch_label"].value_counts().reset_index()
    label_dist.columns = ["label", "count"]
    label_dist.to_csv(tables_dir / "label_distribution.csv", index=False, encoding="utf-8-sig")

    typer.echo(f"  -> 통계 저장 완료 ({tables_dir})")


# ── 8. 리포트 ─────────────────────────────────────────────────────────────────

@app.command()
def build_report(config: str = typer.Option(..., help="설정 파일 경로")):
    """8. 결과 표/그래프 생성"""
    typer.echo("[8/8] 리포트 생성")
    cfg = load_config(config)
    tables_dir = Path(cfg["paths"]["tables"])
    figures_dir = Path(cfg["paths"]["figures"])
    ensure_dir(figures_dir)

    # Mismatch bar chart
    stats = pd.read_csv(tables_dir / "mismatch_stats.csv")
    plt.figure(figsize=(7, 4))
    plt.bar(stats["type"], stats["count"], color=["#e74c3c", "#3498db"])
    plt.title("Disclosure-Practice Mismatch")
    plt.ylabel("Number of Companies")
    plt.tight_layout()
    plt.savefig(figures_dir / "mismatch_bar.png", dpi=150)
    plt.close()

    # Label distribution pie
    labels = pd.read_csv(tables_dir / "label_distribution.csv")
    plt.figure(figsize=(6, 6))
    plt.pie(labels["count"], labels=labels["label"], autopct="%1.1f%%", startangle=140)
    plt.title("Mismatch Label Distribution")
    plt.tight_layout()
    plt.savefig(figures_dir / "label_pie.png", dpi=150)
    plt.close()

    typer.echo(f"  -> 그래프 저장 완료 ({figures_dir})")


# ── 전체 실행 ─────────────────────────────────────────────────────────────────

@app.command()
def run_all(
    config: str = typer.Option(..., help="설정 파일 경로"),
    sample: int = typer.Option(0, help="테스트용 샘플 수 (0=전체)"),
    concurrency: int = typer.Option(3, help="동시 크롤링/탐지 수"),
    skip_collect: bool = typer.Option(False, help="1단계 수집 건너뜀 (companies.csv 재사용)"),
):
    """전체 파이프라인 일괄 실행"""
    typer.echo(f"전체 파이프라인 시작 (sample={sample or 'ALL'}, concurrency={concurrency})")
    if not skip_collect:
        collect_companies(config)
    crawl_sites(config, sample=sample, concurrency=concurrency)
    extract_policies(config)
    detect_trackers(config, concurrency=concurrency)
    evaluate_llm(config)
    compute_mismatch(config)
    run_stats(config)
    build_report(config)
    typer.echo("전체 파이프라인 완료.")


if __name__ == "__main__":
    app()
