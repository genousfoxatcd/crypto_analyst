"""
数据存档工具 (Data Archiver)
每小时自动抓取数据并存档，用于后续分析和模型训练
"""

import json
import gzip
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any


class DataArchiver:
    """
    数据存档工具
    
    功能：
    1. 每小时自动抓取价格和信号数据
    2. 存档到历史文件（按日期组织）
    3. 支持压缩存储（gzip）
    4. 提供数据查询接口
    """
    
    def __init__(self, archive_dir: Path, signal_file: Path, positions_file: Path):
        """
        初始化数据存档工具
        
        Args:
            archive_dir: 存档目录
            signal_file: 当前信号文件路径
            positions_file: 当前持仓文件路径
        """
        self.archive_dir = archive_dir
        self.signal_file = signal_file
        self.positions_file = positions_file
        
        # 创建存档目录
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.price_dir = self.archive_dir / "price"
        self.signal_dir = self.archive_dir / "signal"
        self.positions_dir = self.archive_dir / "positions"
        
        for d in [self.price_dir, self.signal_dir, self.positions_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        print(f"  ✅ 数据存档工具初始化完成")
        print(f"    - 存档目录: {self.archive_dir}")
    
    def archive_hourly_data(self) -> bool:
        """
        存档每小时数据
        
        Returns:
            是否成功
        """
        try:
            now = datetime.now(timezone.utc)
            timestamp = now.strftime("%Y%m%d_%H%M")
            date_str = now.strftime("%Y-%m-%d")
            
            # 1. 存档价格数据
            if self.signal_file.exists():
                signal_data = json.loads(self.signal_file.read_text(encoding='utf-8'))
                prices = signal_data.get("prices", {})
                
                price_archive_file = self.price_dir / f"price_{timestamp}.json"
                price_archive_file.write_text(
                    json.dumps(prices, indent=2, ensure_ascii=False),
                    encoding='utf-8'
                )
                
                print(f"  ✅ 价格数据已存档: {price_archive_file.name}")
            
            # 2. 存档信号数据
            if self.signal_file.exists():
                signal_data = json.loads(self.signal_file.read_text(encoding='utf-8'))
                signals = signal_data.get("signals", {})
                
                signal_archive_file = self.signal_dir / f"signal_{timestamp}.json"
                signal_archive_file.write_text(
                    json.dumps(signals, indent=2, ensure_ascii=False),
                    encoding='utf-8'
                )
                
                print(f"  ✅ 信号数据已存档: {signal_archive_file.name}")
            
            # 3. 存档持仓数据
            if self.positions_file.exists():
                positions_data = json.loads(self.positions_file.read_text(encoding='utf-8'))
                
                positions_archive_file = self.positions_dir / f"positions_{timestamp}.json"
                positions_archive_file.write_text(
                    json.dumps(positions_data, indent=2, ensure_ascii=False),
                    encoding='utf-8'
                )
                
                print(f"  ✅ 持仓数据已存档: {positions_archive_file.name}")
            
            return True
        
        except Exception as e:
            print(f"  ❌ 数据存档失败: {e}")
            return False
    
    def get_historical_prices(self, coin: str, hours: int = 24) -> list:
        """
        获取历史价格数据
        
        Args:
            coin: 币种（如 "BTC"）
            hours: 获取最近N小时的数据
            
        Returns:
            历史价格列表 [{"timestamp": "...", "price": 12345.67}, ...]
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)
        
        results = []
        
        # 遍历价格存档文件
        for file in sorted(self.price_dir.glob("price_*.json"), reverse=True):
            try:
                data = json.loads(file.read_text(encoding='utf-8'))
                
                if coin in data:
                    price_info = data[coin]
                    timestamp_str = file.stem.replace("price_", "")
                    timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M").replace(tzinfo=timezone.utc)
                    
                    if timestamp >= cutoff:
                        results.append({
                            "timestamp": timestamp.isoformat(),
                            "price": price_info.get("price", 0),
                            "change_24h": price_info.get("change_24h", 0),
                            "volume_24h": price_info.get("volume_24h", 0)
                        })
            
            except Exception as e:
                print(f"  ⚠️ 读取价格存档失败 ({file.name}): {e}")
        
        return sorted(results, key=lambda x: x["timestamp"])
    
    def get_historical_signals(self, coin: str = None, hours: int = 24) -> list:
        """
        获取历史信号数据
        
        Args:
            coin: 币种（如 "BTC"），None 表示所有币种
            hours: 获取最近N小时的数据
            
        Returns:
            历史信号列表
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)
        
        results = []
        
        # 遍历信号存档文件
        for file in sorted(self.signal_dir.glob("signal_*.json"), reverse=True):
            try:
                data = json.loads(file.read_text(encoding='utf-8'))
                
                timestamp_str = file.stem.replace("signal_", "")
                timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M").replace(tzinfo=timezone.utc)
                
                if timestamp >= cutoff:
                    if coin:
                        if coin in data:
                            results.append({
                                "timestamp": timestamp.isoformat(),
                                "coin": coin,
                                "signal": data[coin]
                            })
                    else:
                        for c, signal in data.items():
                            results.append({
                                "timestamp": timestamp.isoformat(),
                                "coin": c,
                                "signal": signal
                            })
            
            except Exception as e:
                print(f"  ⚠️ 读取信号存档失败 ({file.name}): {e}")
        
        return sorted(results, key=lambda x: x["timestamp"])
    
    def cleanup_old_archives(self, days: int = 30):
        """
        清理旧存档（保留最近N天）
        
        Args:
            days: 保留天数
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        
        cleaned = 0
        
        for d in [self.price_dir, self.signal_dir, self.positions_dir]:
            for file in d.glob("*.json"):
                try:
                    timestamp_str = file.stem.split("_")[1]
                    timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M").replace(tzinfo=timezone.utc)
                    
                    if timestamp < cutoff:
                        file.unlink()
                        cleaned += 1
                
                except Exception as e:
                    print(f"  ⚠️ 清理旧存档失败 ({file.name}): {e}")
        
        if cleaned > 0:
            print(f"  ✅ 清理旧存档: {cleaned} 个文件")


def test_data_archiver():
    """测试数据存档工具"""
    print("=" * 60)
    print("  测试数据存档工具")
    print("=" * 60)
    
    # 创建测试目录
    test_archive_dir = Path("/tmp/test_archive")
    test_signal_file = Path("/tmp/test_signal.json")
    test_positions_file = Path("/tmp/test_positions.json")
    
    # 创建测试数据
    test_signal_data = {
        "prices": {
            "BTC": {"price": 67000.0, "change_24h": 2.5, "volume_24h": 32000000000},
            "ETH": {"price": 3500.0, "change_24h": 1.8, "volume_24h": 15000000000}
        },
        "signals": {
            "BTC": {"direction": "LONG", "prob_score": 65.0, "entry_price": 66800.0},
            "ETH": {"direction": "SHORT", "prob_score": 58.0, "entry_price": 3520.0}
        }
    }
    
    test_positions_data = {
        "BTC": {"direction": "LONG", "entry_price": 66800.0, "size": 0.1},
        "ETH": {"direction": "SHORT", "entry_price": 3520.0, "size": 1.0}
    }
    
    test_signal_file.write_text(json.dumps(test_signal_data, indent=2, ensure_ascii=False), encoding='utf-8')
    test_positions_file.write_text(json.dumps(test_positions_data, indent=2, ensure_ascii=False), encoding='utf-8')
    
    # 创建数据存档工具
    archiver = DataArchiver(test_archive_dir, test_signal_file, test_positions_file)
    
    # 测试存档
    print("\n  测试存档:")
    success = archiver.archive_hourly_data()
    print(f"  存档结果: {'成功' if success else '失败'}")
    
    # 测试查询
    print("\n  测试查询历史价格:")
    btc_prices = archiver.get_historical_prices("BTC", hours=1)
    print(f"  BTC 最近1小时价格数据: {len(btc_prices)} 条")
    
    print("\n" + "=" * 60)
    print("  测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    test_data_archiver()
