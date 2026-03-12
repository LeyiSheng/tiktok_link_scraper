"""
login_server.py — 在服务器（无头模式）下提取二维码并完成登录

运行方式：
    python3 login_server.py

原理：
    1. 使用与主程序相同的 browser_data 持久化目录启动浏览器
    2. 打开 douyin.com，拉起登录弹窗
    3. 截取登录二维码并保存为 qrcode.png
    4. 等待用户扫码，检测登录成功后保存截图并退出
"""

import asyncio
import sys
from pathlib import Path

import config
from scraper.browser import create_stealth_page, close_browser

async def main():
    # 强制在无头模式下运行
    config.HEADLESS = True
    
    print("🚀 正在启动浏览器...")
    page = await create_stealth_page()
    
    print("🌐 正在打开抖音首页...")
    await page.goto("https://www.douyin.com/", wait_until="networkidle")
    await asyncio.sleep(3)
    
    # 检测是否已经登录（通过查找右上角的发视频按钮或头像）
    is_logged_in = await page.evaluate("""() => {
        return !!document.cookie.match(/sessionid_ss=/);
    }""")
    
    if is_logged_in:
        print("✅ 检测到已存在登录凭证，无需重复登录！")
        await page.screenshot(path="login_success.png")
        print("📸 当前截图已保存为 login_success.png")
        await close_browser()
        return

    print("🔍 未登录，正在尝试拉起登录窗口...")
    # 尝试寻找登录按钮并点击
    try:
        # 有时候抖音会自动弹登录框，如果没有我们主动点右上角登录
        login_btn = page.locator('button:has-text("登录"), div:has-text("登录")').locator('visible=true').first
        if await login_btn.count() > 0:
            await login_btn.click()
            await asyncio.sleep(2)
    except Exception as e:
        print(f"点击登录按钮失败: {e}")

    print("📸 正在截取登录状态图...")
    await page.screenshot(path="qrcode.png", full_page=True)
    print("\n" + "="*50)
    print("🎉 请将服务器上的 qrcode.png 下载到本地，或者用看图软件打开。")
    print("📱 请打开手机抖音扫描二维码进行登录。")
    print("="*50 + "\n")
    
    print("⏳ 等待扫码中（最多等待 120 秒）...")
    
    logged_in = False
    for i in range(60):
        await asyncio.sleep(2)
        
        # 通过判断 cookie 中是否有 sessionid_ss 来确定是否登录成功
        is_logged_in = await page.evaluate("""() => {
            return !!document.cookie.match(/sessionid_ss=/);
        }""")
        
        if is_logged_in:
            logged_in = True
            break
            
        # 顺便每隔 10 秒更新一下截图，防止二维码过期刷新了用户看不见
        if i > 0 and i % 5 == 0:
            await page.screenshot(path="qrcode.png", full_page=True)
            print("🔄 二维码截图已更新 (qrcode.png)...")
            
    if logged_in:
        print("\n✅ 扫码成功！登录完成！")
        await asyncio.sleep(3) # 等待页面刷新加载完
        await page.screenshot(path="login_success.png", full_page=True)
        print("📸 登录成功后的截图已保存为 login_success.png")
    else:
        print("\n❌ 扫码超时，请重新运行本脚本。")
        
    print("🧹 正在清理关闭浏览器...")
    await close_browser()
    print("✨ 请运行 python3 main.py --headless 开始抓取！")

if __name__ == "__main__":
    asyncio.run(main())
