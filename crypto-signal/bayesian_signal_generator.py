"""
贝叶斯信号生成器 (Bayesian Signal Generator)
使用贝叶斯定理动态计算信号胜率，替代固定的 prob_score 规则计算
"""

import json
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

# 支持的交易对
COINS = ["BTC", "ETH", "SOL", "DOGE", "BNB", "TAO", "ZEC", "CAKE", "PAXG", "HYPE", "TRX", "AAVE"]

# 因子名称列表
FACTOR_NAMES = ['bb_pos', 'funding_rate', 'long_pct', 'taker_bsr', 'flow_buy', 'change_24h']


class BayesianSignalGenerator:
    """
    贝叶斯信号生成器
    
    核心原理：
    P(盈利|当前信号) = P(当前信号|盈利) × P(盈利) / P(当前信号)
    
    其中：
    - P(盈利)：先验概率（历史胜率）
    - P(当前信号|盈利)：似然概率（盈利交易中该信号组合出现的频率）
    - P(当前信号)：证据（该信号组合在历史中出现的总频率）
    """
    
    def __init__(self, history_file: Path, lookback_days: int = 30):
        """
        初始化贝叶斯信号生成器
        
        Args:
            history_file: 历史交易数据文件路径 (paper_trade_history.json)
            lookback_days: 回溯天数（只使用最近N天的数据）
        """
        self.history_file = history_file
        self.lookback_days = lookback_days
        self.history = []
        
        # 先验概率 P(盈利|币种)
        self.prior_probs = {}
        
        # 似然概率 P(因子区间|盈利) 和 P(因子区间|亏损)
        self.likelihoods = {"win": {}, "loss": {}}
        
        # 证据 P(因子区间)
        self.evidence = {}
        
        # 加载历史数据并计算
        self._load_history()
        self._calc_prior()
        self._calc_likelihood()
    
    def _discretize_factor(self, factor_name: str, value) -> Optional[str]:
        """
        将连续因子离散化为区间
        
        Args:
            factor_name: 因子名称
            value: 因子值
            
        Returns:
            离散区间标签（如 "very_low", "low", "medium", "high", "very_high"）
        """
        if value is None:
            return None
        
        if factor_name == "bb_pos":
            # BB位置 (pos_pct): <20%, 20-40%, 40-60%, 60-80%, >80%
            if value < 20:
                return "very_low"
            elif value < 40:
                return "low"
            elif value < 60:
                return "medium"
            elif value < 80:
                return "high"
            else:
                return "very_high"
        
        elif factor_name == "funding_rate":
            # 资金费率: <-0.001, -0.001~0, 0~0.001, >0.001
            if value < -0.001:
                return "very_negative"
            elif value < 0:
                return "negative"
            elif value < 0.001:
                return "neutral"
            else:
                return "positive"
        
        elif factor_name == "long_pct":
            # 多空比: <40%, 40-50%, 50-60%, >60%
            if value < 40:
                return "very_low"
            elif value < 50:
                return "low"
            elif value < 60:
                return "medium"
            else:
                return "high"
        
        elif factor_name == "taker_bsr":
            # 吃单比: <0.7, 0.7-1.0, 1.0-1.3, >1.3
            if value < 0.7:
                return "very_low"
            elif value < 1.0:
                return "low"
            elif value < 1.3:
                return "medium"
            else:
                return "high"
        
        elif factor_name == "flow_buy":
            # 资金流: <42%, 42-50%, 50-58%, >58%
            if value < 42:
                return "very_low"
            elif value < 50:
                return "low"
            elif value < 58:
                return "medium"
            else:
                return "high"
        
        elif factor_name == "change_24h":
            # 涨跌幅: <-8%, -8~-3%, -3~0%, 0~3%, 3~8%, >8%
            if value < -8:
                return "very_negative"
            elif value < -3:
                return "negative"
            elif value < 0:
                return "slightly_negative"
            elif value < 3:
                return "slightly_positive"
            elif value < 8:
                return "positive"
            else:
                return "very_positive"
        
        return None
    
    def _load_history(self):
        """加载历史交易数据"""
        if not self.history_file.exists():
            print(f"  ⚠️  历史交易文件不存在: {self.history_file}")
            self.history = []
            return
        
        try:
            self.history = json.loads(self.history_file.read_text(encoding='utf-8'))
            
            # 只保留最近N天的交易
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).isoformat()
            self.history = [h for h in self.history if h.get("closed_at", "") >= cutoff]
            
            print(f"  ✅ 加载历史交易数据: {len(self.history)} 条 (最近 {self.lookback_days} 天)")
        except Exception as e:
            print(f"  ❌ 加载历史交易数据失败: {e}")
            self.history = []
    
    def _calc_prior(self):
        """
        计算先验概率 P(盈利)
        
        按币种计算：P(盈利|币种) = 该币种历史盈利交易数 / 该币种总交易数
        冷启动处理：若无历史数据，使用0.5（均匀先验）
        """
        for coin in COINS:
            trades = [h for h in self.history 
                     if h.get("coin") == coin and h.get("filled_at")]
            
            if not trades:
                # 冷启动：使用均匀先验
                self.prior_probs[coin] = 0.5
            else:
                wins = [h for h in trades if h.get("pnl_usd", 0) > 0]
                win_rate = len(wins) / len(trades)
                # 平滑处理：避免 0 或 1（使用拉普拉斯平滑）
                win_count = len(wins)
                total_count = len(trades)
                win_rate_smooth = (win_count + 1) / (total_count + 2)
                self.prior_probs[coin] = win_rate_smooth
                
                print(f"  📊 {coin} 先验概率: {win_rate:.1%} ({len(wins)}/{len(trades)})")
    
    def _calc_likelihood(self):
        """
        计算似然概率 P(因子区间|盈利) 和 P(因子区间|亏损)
        
        遍历历史交易数据，对每个因子区间计算：
        - P(因子区间|盈利) = 盈利交易中该区间出现次数 / 总盈利交易数
        - P(因子区间|亏损) = 亏损交易中该区间出现次数 / 总亏损交易数
        
        使用拉普拉斯平滑避免0概率问题
        """
        # 初始化计数器
        win_counts = defaultdict(lambda: defaultdict(int))
        loss_counts = defaultdict(lambda: defaultdict(int))
        
        # 统计每个因子区间在盈利/亏损交易中出现的次数
        for trade in self.history:
            if not trade.get("filled_at"):
                continue
            
            is_win = trade.get("pnl_usd", 0) > 0
            factors = trade.get("signal", {}).get("factors", {})
            
            for factor_name in FACTOR_NAMES:
                value = factors.get(factor_name)
                if value is None:
                    continue
                
                disc = self._discretize_factor(factor_name, value)
                if disc is None:
                    continue
                
                if is_win:
                    win_counts[factor_name][disc] += 1
                else:
                    loss_counts[factor_name][disc] += 1
        
        # 转换为概率（使用拉普拉斯平滑避免0概率）
        self.likelihoods = {"win": {}, "loss": {}}
        
        for factor_name in FACTOR_NAMES:
            self.likelihoods["win"][factor_name] = {}
            self.likelihoods["loss"][factor_name] = {}
            
            total_wins = sum(win_counts[factor_name].values())
            total_losses = sum(loss_counts[factor_name].values())
            
            # 获取所有出现过的离散区间
            all_discs = set(list(win_counts[factor_name].keys()) + list(loss_counts[factor_name].keys()))
            
            for disc in all_discs:
                # 拉普拉斯平滑：+1 / +2（避免0概率）
                win_count = win_counts[factor_name].get(disc, 0)
                loss_count = loss_counts[factor_name].get(disc, 0)
                
                self.likelihoods["win"][factor_name][disc] = (win_count + 1) / (total_wins + 2)
                self.likelihoods["loss"][factor_name][disc] = (loss_count + 1) / (total_losses + 2)
    
    def calc_posterior(self, coin: str, signal: dict) -> float:
        """
        计算后验概率 P(盈利|当前信号)
        
        使用朴素贝叶斯（假设因子独立）：
        P(盈利|因子1,因子2,...,因子6) 
        ∝ P(因子1|盈利) × P(因子2|盈利) × ... × P(因子6|盈利) × P(盈利)
        
        使用对数概率避免下溢
        
        Args:
            coin: 币种（如 "BTC"）
            signal: 信号数据（包含各因子的值）
            
        Returns:
            后验概率（0-100%）
        """
        # 获取先验概率
        prior = self.prior_probs.get(coin, 0.5)
        prior_smooth = max(1e-10, min(1 - 1e-10, prior))  # 平滑处理
        
        # 使用对数概率避免下溢
        # log(P(盈利|因子)) = log(P(因子|盈利)) + log(P(盈利)) - log(P(因子))
        # 简化计算：计算 log(P(盈利|因子) / P(亏损|因子)) 比值
        
        log_p_win = math.log(prior_smooth)  # log(P(盈利))
        log_p_loss = math.log(1 - prior_smooth)  # log(P(亏损))
        
        # 遍历所有因子，累加对数似然
        for factor_name in FACTOR_NAMES:
            value = signal.get(factor_name)
            if value is None:
                continue
            
            disc = self._discretize_factor(factor_name, value)
            if disc is None:
                continue
            
            # 获取似然概率（使用拉普拉斯平滑后的概率）
            p_win = self.likelihoods.get("win", {}).get(factor_name, {}).get(disc, 0.01)
            p_loss = self.likelihoods.get("loss", {}).get(factor_name, {}).get(disc, 0.01)
            
            # 平滑处理：确保 > 0
            p_win = max(1e-10, p_win)
            p_loss = max(1e-10, p_loss)
            
            # 累加对数似然
            log_p_win += math.log(p_win)
            log_p_loss += math.log(p_loss)
        
        # 计算后验概率（使用 softmax 技巧）
        # P(盈利|因子) = e^log_p_win / (e^log_p_win + e^log_p_loss)
        # 避免数值溢出：减去最大值
        max_log = max(log_p_win, log_p_loss)
        exp_win = math.exp(log_p_win - max_log)
        exp_loss = math.exp(log_p_loss - max_log)
        
        posterior = exp_win / (exp_win + exp_loss)
        
        # 限制范围（避免极端值）
        posterior = min(0.95, max(0.05, posterior))
        
        return round(posterior * 100, 1)  # 返回 5%-95%

    def update_from_trade(self, trade: dict):
        """
        根据一笔新交易更新贝叶斯模型
        
        当模拟交易系统完成一笔交易（TP/SL触发）时，
        调用此方法更新历史数据统计，重新计算先验概率和似然概率
        
        Args:
            trade: 交易数据字典（包含 coin, direction, pnl_usd, signal 等）
        """
        # 将新交易添加到历史数据
        self.history.append(trade)
        
        # 重新计算先验概率和似然概率
        self._calc_prior()
        self._calc_likelihood()
        
        print(f"  ✅ 贝叶斯模型已更新（新增交易: {trade.get('coin')} {trade.get('direction')} PnL={trade.get('pnl_usd', 0):.2f}）")
    
    def save_training_data(self, output_file: Path):
        """
        保存训练数据（历史交易数据）
        
        Args:
            output_file: 输出文件路径
        """
        try:
            output_file.write_text(json.dumps(self.history, indent=2, ensure_ascii=False), encoding='utf-8')
            print(f"  ✅ 训练数据已保存: {output_file}")
        except Exception as e:
            print(f"  ❌ 保存训练数据失败: {e}")

    def get_factor_weights(self, coin: str = None) -> dict:
        """
        从贝叶斯似然概率中提取因子权重建议
        
        原理：
        - 比较 P(因子区间|盈利) vs P(因子区间|亏损) 的区分度
        - 区分度越高 = 因子越有效 = 权重越大
        - 使用 KL 散度度量每个因子的信息增益
        
        Returns:
            {
                "factors": {factor_name: weight_suggestion},
                "prior_win_rate": float,
                "total_trades": int,
                "summary": str
            }
        """
        factor_scores = {}
        total_info_gain = 0.0
        
        for factor_name in FACTOR_NAMES:
            win_likely = self.likelihoods.get("win", {}).get(factor_name, {})
            loss_likely = self.likelihoods.get("loss", {}).get(factor_name, {})
            
            if not win_likely or not loss_likely:
                factor_scores[factor_name] = 0.0
                continue
            
            # 计算 KL 散度：Σ P(win|bin) × log(P(win|bin) / P(loss|bin))
            kl_divergence = 0.0
            for disc in set(list(win_likely.keys()) + list(loss_likely.keys())):
                p_w = max(1e-10, win_likely.get(disc, 0.01))
                p_l = max(1e-10, loss_likely.get(disc, 0.01))
                kl_divergence += p_w * math.log(p_w / p_l)
            
            # 取绝对值（对称化 KL 散度）
            kl_rev = 0.0
            for disc in set(list(win_likely.keys()) + list(loss_likely.keys())):
                p_w = max(1e-10, win_likely.get(disc, 0.01))
                p_l = max(1e-10, loss_likely.get(disc, 0.01))
                kl_rev += p_l * math.log(p_l / p_w)
            
            info_gain = max(0.0, (kl_divergence + kl_rev) / 2)
            factor_scores[factor_name] = info_gain
            total_info_gain += info_gain
        
        # 归一化为权重建议
        weights = {}
        if total_info_gain > 0:
            for fname, gain in factor_scores.items():
                weights[fname] = round(gain / total_info_gain * 100, 1)
        else:
            # 冷启动：均匀权重
            for fname in FACTOR_NAMES:
                weights[fname] = round(100.0 / len(FACTOR_NAMES), 1)
        
        # 提取胜率
        prior_wr = self.prior_probs.get(coin, 0.5) if coin else 0.5
        
        # 生成摘要
        top_factors = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:3]
        summary = f"胜率={prior_wr:.1%} | 最佳因子: {', '.join([f'{f}={w:.0f}%' for f,w in top_factors])}"
        
        return {
            "factors": weights,
            "prior_win_rate": round(prior_wr * 100, 1),
            "total_trades": len(self.history),
            "summary": summary,
        }

    def save_learning_log(self, output_file: Path):
        """
        保存贝叶斯学习日志（包括因子权重、胜率变化等）
        
        Args:
            output_file: 日志输出文件路径
        """
        try:
            # 读取已有日志
            if output_file.exists():
                log = json.loads(output_file.read_text(encoding='utf-8'))
            else:
                log = {"updates": [], "created": datetime.now(timezone.utc).isoformat()}
            
            # 添加新快照
            snapshot = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_trades": len(self.history),
                "prior_probs": self.prior_probs,
                "factor_weights": self.get_factor_weights()["factors"],
                "summary": self.get_factor_weights()["summary"],
            }
            log["updates"].append(snapshot)
            log["last_updated"] = snapshot["timestamp"]
            
            # 只保留最近50条记录
            if len(log["updates"]) > 50:
                log["updates"] = log["updates"][-50:]
            
            output_file.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')
            print(f"  📝 贝叶斯学习日志已更新: {output_file}")
        except Exception as e:
            print(f"  ⚠️ 保存学习日志失败: {e}")


def test_bayesian_generator():
    """测试贝叶斯信号生成器"""
    print("=" * 60)
    print("  测试贝叶斯信号生成器")
    print("=" * 60)
    
    # 创建测试历史数据
    test_history = [
        {
            "coin": "BTC",
            "direction": "LONG",
            "filled_at": "2026-05-20T10:00:00+00:00",
            "closed_at": "2026-05-21T10:00:00+00:00",
            "pnl_usd": 150.0,
            "signal": {
                "factors": {
                    "bb_pos": 25.0,      # very_low
                    "funding_rate": -0.0005,  # negative
                    "long_pct": 55.0,     # medium
                    "taker_bsr": 1.1,     # medium
                    "flow_buy": 52.0,      # medium
                    "change_24h": -2.0      # slightly_negative
                }
            }
        },
        {
            "coin": "BTC",
            "direction": "LONG",
            "filled_at": "2026-05-21T10:00:00+00:00",
            "closed_at": "2026-05-22T10:00:00+00:00",
            "pnl_usd": -80.0,
            "signal": {
                "factors": {
                    "bb_pos": 45.0,      # low
                    "funding_rate": 0.0002,  # neutral
                    "long_pct": 52.0,     # medium
                    "taker_bsr": 0.9,     # low
                    "flow_buy": 48.0,      # low
                    "change_24h": 1.5       # slightly_positive
                }
            }
        },
        {
            "coin": "ETH",
            "direction": "SHORT",
            "filled_at": "2026-05-20T10:00:00+00:00",
            "closed_at": "2026-05-21T10:00:00+00:00",
            "pnl_usd": 200.0,
            "signal": {
                "factors": {
                    "bb_pos": 75.0,      # high
                    "funding_rate": 0.0015,  # positive
                    "long_pct": 45.0,     # low
                    "taker_bsr": 1.2,     # medium
                    "flow_buy": 45.0,      # low
                    "change_24h": 5.0       # positive
                }
            }
        }
    ]
    
    # 保存测试数据
    test_file = Path("/tmp/test_trade_history.json")
    test_file.write_text(json.dumps(test_history, indent=2), encoding='utf-8')
    
    # 创建贝叶斯信号生成器
    generator = BayesianSignalGenerator(test_file, lookback_days=30)
    
    # 测试后验概率计算
    print("\n  测试后验概率计算:")
    test_signal = {
        "bb_pos": 30.0,      # very_low
        "funding_rate": -0.0008,  # negative
        "long_pct": 58.0,     # medium
        "taker_bsr": 1.0,     # medium
        "flow_buy": 51.0,      # medium
        "change_24h": -1.0      # slightly_negative
    }
    
    btc_posterior = generator.calc_posterior("BTC", test_signal)
    eth_posterior = generator.calc_posterior("ETH", test_signal)
    
    print(f"  BTC 后验概率: {btc_posterior:.1f}%")
    print(f"  ETH 后验概率: {eth_posterior:.1f}%")
    
    # 测试模型更新
    print("\n  测试模型更新:")
    new_trade = {
        "coin": "BTC",
        "direction": "LONG",
        "filled_at": "2026-05-23T10:00:00+00:00",
        "closed_at": "2026-05-24T10:00:00+00:00",
        "pnl_usd": 120.0,
        "signal": {
            "factors": {
                "bb_pos": 35.0,
                "funding_rate": -0.0003,
                "long_pct": 54.0,
                "taker_bsr": 1.05,
                "flow_buy": 50.0,
                "change_24h": 0.5
            }
        }
    }
    
    generator.update_from_trade(new_trade)
    
    # 再次计算后验概率（应该发生变化）
    btc_posterior_new = generator.calc_posterior("BTC", test_signal)
    print(f"  BTC 后验概率（更新后）: {btc_posterior_new:.1f}%")
    
    print("\n" + "=" * 60)
    print("  测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    test_bayesian_generator()
