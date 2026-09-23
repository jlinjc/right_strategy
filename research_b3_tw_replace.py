"""research_b3_tw_replace.py - 待辦③:00757(統一FANG+,美股科技台幣計價)的替代標的
========================================================================
Jason:「我本來就有投資美股,不要 00757,幫我換台股標的。」
→ 在 4 檔基底(0050/0052/006208/0051)上,逐一測「第 5 檔要不要放、放誰」。
規則與 live 一致:等權、各自 200MA 擇時、台股信用哨(HYG+LQD+SOXX)乘數、固定1倍、成本 0.1%/邊。
每個候選只能在「它自己有資料的期間」比較,所以每次都用同期的 4 檔基底當對照(公平比較)。
事前判準:ΔSharpe ≥ +0.10 且 p<0.10(同期比較)才算「值得放」;否則就是 4 檔。
另外標註:該標的是不是「純台股」(Jason 要避開美股重複曝險)。
用法: python research_b3_tw_replace.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R
import research_b1_credit_canary as C1

BASE = ['0050.TW', '0052.TW', '006208.TW', '0051.TW']
CANDS = {
    '00757.TW': ('統一FANG+', '美股(FANG+)', '現行第5檔'),
    '0056.TW':  ('元大高股息', '純台股', '高股息'),
    '00692.TW': ('富邦公司治理100', '純台股', '大型股ESG篩選'),
    '00733.TW': ('富邦中小', '純台股', '中小型'),
    '00895.TW': ('富邦未來車', '純台股為主', '主題'),
    '00913.TW': ('兆豐台灣晶圓製造', '純台股', '主題:晶圓'),
    '00921.TW': ('兆豐龍頭等權重', '純台股', '龍頭等權'),
    '00891.TW': ('中信關鍵半導體', '純台股', '主題:半導體'),
    '00892.TW': ('富邦台灣半導體', '純台股', '主題:半導體'),
    '00762.TW': ('元大全球AI', '全球(美股為主)', '主題'),
    '0050.TW':  ('(不放第5檔)', '—', '只用4檔'),
}
CREDIT = C1.VARIANTS['TRIPLE_sm']       # 台股 live 信用哨


def ew_credit(P):
    pl = R.ew_plan(P, reb=R.reb_days(P.idx, 0))
    h = C1.health(P.idx, CREDIT['tks'], CREDIT['mode'])
    return R.Plan(S=pl.S, E=pl.E * h[:, None], reb=pl.reb)


def main():
    import research_q4_choices as Q4
    Q4.ensure(list(CANDS))
    rows = []
    for tk, (name, kind, note) in CANDS.items():
        tks = BASE if tk == '0050.TW' else BASE + [tk]
        try:
            P = R.build_panel('TW4', tickers=tks)
        except Exception as e:
            rows.append({'第5檔': f'{tk} {name}', '性質': kind, '結果': f'建panel失敗 {e}'}); continue
        if P.n < 252 * 3:
            rows.append({'第5檔': f'{tk} {name}', '性質': kind, '起點': str(P.idx[0].date()),
                         '結果': f'期間僅 {P.n/252:.1f} 年(<3年)不判定'}); continue
        r = R.run_plan(P, ew_credit(P))
        m = R.metrics(r.r, P.rf, res=r)
        # 同期 4 檔基底
        Pb = R.build_panel('TW4', tickers=BASE, start=str(P.idx[0].date()))
        rb = R.run_plan(Pb, ew_credit(Pb))
        mb = R.metrics(rb.r, Pb.rf)
        IDX = R.sb_indices(min(P.n, Pb.n), B=1000, mean_block=63, seed=0)
        n = min(len(r.r), len(rb.r))
        bs = R.boot_delta_sharpe(r.r.values[-n:], rb.r.values[-n:], P.rf[-n:], IDX[:, :n] % n)
        rows.append({'第5檔': f'{tk} {name}', '性質': kind, '起點': str(P.idx[0].date()),
                     '年數': round(P.n / 252, 1), 'CAGR%': round(m['CAGR'], 1),
                     'Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     '同期4檔Sharpe': round(mb['ShEx'], 2), 'ΔSharpe': round(m['ShEx'] - mb['ShEx'], 3),
                     'p': round(bs['p'], 3),
                     '判定': ('值得放' if (m['ShEx'] - mb['ShEx']) >= 0.10 and bs['p'] < 0.10 else '不值得')})
    df = pd.DataFrame(rows)
    print('=' * 130); print('台股第5檔:放誰?(4檔基底 0050/0052/006208/0051,等權+信用哨,同期比較)')
    print('=' * 130); print(df.to_string(index=False))
    print('\n判準:ΔSharpe ≥ +0.10 且 p<0.10 才算「值得放」;各候選只能在自己有資料的期間比,所以年數不同。')


if __name__ == '__main__':
    main()
