"""Local browser acceptance checks; uses the actual service and persisted demo."""
import json, os, argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--exercise-choices',action='store_true')
    parser.add_argument('--comparison-task',help='Optional archived episode ID to display in the comparison screenshot')
    args=parser.parse_args()
    demo=json.loads(Path('.local/recovery-browser-fixture.json' if args.exercise_choices else '.local/recovery-v2-demo.json').read_text())
    output=Path('.local/recovery-v2-browser');output.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_PATH') or p.chromium.executable_path,headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script(f"localStorage.setItem('verireturn.agentThread.U001', {json.dumps(demo['thread_id'])});localStorage.setItem('verireturn.opsActor','OPS_MANAGER');")
        page.goto('http://127.0.0.1:5173/agent');page.wait_for_load_state('networkidle')
        page.get_by_text('售后恢复',exact=True).first.wait_for(timeout=30000)
        assert page.get_by_role('button',name='确认执行',exact=True).count()==0, 'resolved confirmation returned after refresh'
        if args.exercise_choices:
            page.get_by_role('button',name='改为退款',exact=True).click()
            page.get_by_role('dialog').wait_for()
            page.get_by_role('button',name='确认执行',exact=True).click()
            page.get_by_role('dialog').wait_for(state='hidden')
            page.get_by_text('方案已执行',exact=True).first.wait_for()
            page.reload();page.get_by_text('方案已执行',exact=True).first.wait_for()
            assert page.get_by_role('button',name='确认执行',exact=True).count()==0

        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'customer page overflows horizontally'
        page.screenshot(path=str(output/'customer-recovery.png'),full_page=True)
        page.reload();page.wait_for_load_state('networkidle');assert page.get_by_text('售后恢复',exact=True).count()>0
        page.goto('http://127.0.0.1:5173/');page.wait_for_load_state('networkidle')
        page.get_by_role('button',name='查看明细').first.click()
        page.get_by_role('columnheader',name='业务完成',exact=True).wait_for();page.wait_for_function("document.querySelector('[role=dialog]')?.getBoundingClientRect().width > 900")
        if args.comparison_task:
            selector=page.get_by_role('combobox',name='选择评测任务')
            selector.focus();selector.press('ArrowDown')
            # Ant Design uses a virtual list; keyboard navigation renders each option.
            for _ in range(300):
                if page.locator('.ant-select-item-option-active').inner_text()==args.comparison_task:
                    selector.press('Enter');break
                selector.press('ArrowDown')
            else:
                raise AssertionError(f'Comparison task not present in loaded results: {args.comparison_task}')
            page.get_by_role('dialog').get_by_title(args.comparison_task,exact=True).wait_for()
        dialog=page.get_by_role('dialog')
        bounds=dialog.bounding_box()
        if bounds and bounds['height']+160>page.viewport_size['height']:
            page.set_viewport_size({'width':1440,'height':int(bounds['height'])+200})
        page.get_by_role('dialog').screenshot(path=str(output/'strategy-comparison.png'),animations='disabled')
        assert not errors,errors
        browser.close()
    print(json.dumps({'customer_refresh_restore':True,'comparison_rendered':True,'customer_reconfirmation':args.exercise_choices,'browser_errors':errors,'screenshots':str(output)},ensure_ascii=False))

if __name__=='__main__':main()
