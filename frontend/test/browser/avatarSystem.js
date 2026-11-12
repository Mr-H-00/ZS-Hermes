// 已登录开发环境运行：playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/avatarSystem.js
// prettier-ignore
async (page) => {
  const check=(value,message)=>{if(!value)throw Error(message)}
  const origin=await page.evaluate(()=>location.origin)
  const errors=[]
  const external=[]
  const errorListener=error=>errors.push(error.message)
  const requestListener=request=>{if(request.url().includes('api.dicebear.com'))external.push(request.url())}
  page.on('pageerror',errorListener)
  page.on('request',requestListener)
  await page.goto(`${origin}/agent`)
  await page.locator('[contenteditable=true]').first().waitFor()
  await page.locator('.fallback-avatar[data-avatar-kind=agent] img').first().evaluate(img=>img.decode())
  await page.locator('.fallback-avatar[data-avatar-kind=user] img').first().evaluate(img=>img.decode())
  const selected=await page.locator('.fallback-avatar[data-avatar-kind=agent]').first().getAttribute('data-avatar-seed')
  await page.goto(`${origin}/agent-manage`)
  await page.locator('.agent-card .fallback-avatar img').first().waitFor()
  const selectedCard=page.locator('.agent-card .fallback-avatar').filter({has:page.locator('img')})
  check(await selectedCard.count()>0,'Agent management has no actual images')
  check(await page.locator(`.agent-card [data-avatar-seed="${selected}"]`).count()>0,'Selected Agent missing from directory')
  const originalTheme=await page.evaluate(()=>localStorage.getItem('theme'))
  try {
    await page.evaluate(async()=>{
      const {createApp,h,reactive}=await import('/node_modules/.vite/deps/vue.js')
      const {default:Avatar}=await import('/src/shared/ui/FallbackAvatar.vue')
      const {useThemeStore}=await import('/src/shared/model/theme.js')
      const parent=document.createElement('div');parent.id='avatar-probe'
      parent.style.cssText='position:fixed;inset:0;z-index:9999;background:var(--gray-0);color:var(--gray-900);padding:32px;overflow:auto'
      document.body.append(parent)
      const state=reactive({style:'gaze',preset:'studio',dark:'auto',shape:'square',src:'',kind:'agent',seed:'fixture-42',alt:'测试头像',decorative:false})
      const failures=[]
      const app=createApp({setup:()=>()=>h('div',[h('h2','头像状态验证'),h(Avatar,{...state,size:96,onFallback:e=>failures.push(e.reason)})])})
      app.mount(parent)
      window.avatarProbe={state,failures,app,theme:useThemeStore()}
    })
    await page.evaluate(()=>{window.avatarProbe.state.src=null;window.avatarProbe.state.name=null})
    await page.locator('#avatar-probe img').evaluate(img=>img.decode())
    const svg=()=>page.locator('#avatar-probe img').evaluate(img=>decodeURIComponent(img.src.split(',').slice(1).join(',')))
    const pixels=()=>page.locator('#avatar-probe img').evaluate(async img=>{
      await img.decode();const c=document.createElement('canvas');c.width=c.height=256
      const ctx=c.getContext('2d');ctx.drawImage(img,0,0,256,256)
      return [...ctx.getImageData(0,0,1,1).data]
    })
    for(const style of ['glyphs','clay','shape-grid','gaze']) for(const preset of ['default','studio','heritage','soft']) for(const dark of ['off','on']) {
      await page.evaluate(config=>Object.assign(window.avatarProbe.state,config),{style,preset,dark})
      const corner=await pixels()
      check(corner[3]===(style==='gaze'?0:255),`${style}/${preset}/${dark} canvas alpha`)
    }
    await page.evaluate(()=>Object.assign(window.avatarProbe.state,{style:'glyphs',preset:'studio',dark:'auto',shape:'circle',kind:'user'}))
    const corner=await pixels();check(corner[3]===0,'Circular clipping missing')
    await page.evaluate(()=>window.avatarProbe.theme.setTheme(false));const light=await svg()
    await page.evaluate(()=>window.avatarProbe.theme.setTheme(true));const night=await svg()
    check(light!==night,'auto did not follow application theme')
    await page.evaluate(()=>window.avatarProbe.state.dark='off');check(await svg()===light,'off did not override theme')
    await page.evaluate(()=>window.avatarProbe.state.dark='on');check(await svg()===night,'on did not override theme')
    await page.context().setOffline(true)
    await page.evaluate(()=>window.avatarProbe.state.seed='fixture-offline')
    await page.locator('#avatar-probe img').evaluate(img=>img.decode())
    await page.context().setOffline(false)
    const externalUrl=`${origin}/avatar-probe-unreachable.svg`
    await page.route('**/avatar-probe-unreachable.svg',route=>route.abort('internetdisconnected'))
    await page.evaluate(src=>window.avatarProbe.state.src=src,externalUrl)
    await page.locator('#avatar-probe [data-avatar-state=fallback]').waitFor()
    check(await page.locator('#avatar-probe .lucide-user').count()===1,'User fallback icon missing')
    check(await page.locator('#avatar-probe [role=img]').getAttribute('aria-label')==='测试头像','Fallback accessible name missing')
    await page.evaluate(()=>window.avatarProbe.state.kind='agent')
    check(await page.locator('#avatar-probe .lucide-bot').count()===1,'Agent fallback icon missing')
    const background=await page.locator('#avatar-probe .fallback-avatar').evaluate(el=>getComputedStyle(el).backgroundColor)
    check(background==='rgb(237, 242, 251)','Night fallback does not keep pale background')
    const recovered=await page.evaluate(async()=>{
      const {generate,imageUrl}=await import('/src/shared/lib/avatar/generator.js')
      return imageUrl(generate({style:'gaze',seed:'fixture-upload'}))
    })
    await page.evaluate(src=>window.avatarProbe.state.src=src,recovered)
    await page.locator('#avatar-probe img').evaluate(img=>img.decode())
    check(await page.locator('#avatar-probe img').getAttribute('src')===recovered,'Custom image not preferred')
    await page.evaluate(()=>{
      const current=document.querySelector('#avatar-probe img')
      window.avatarProbe.state.src=''
      window.avatarProbe.oldImage=current
    })
    await page.locator('#avatar-probe img').evaluate(img=>img.decode())
    await page.evaluate(()=>window.avatarProbe.oldImage.dispatchEvent(new Event('error')))
    check(await page.locator('#avatar-probe [data-avatar-state=fallback]').count()===0,'Stale error replaced new source')
    await page.evaluate(src=>window.avatarProbe.state.src=src,externalUrl)
    await page.locator('#avatar-probe [data-avatar-state=fallback]').waitFor()
    await page.unroute('**/avatar-probe-unreachable.svg')
    await page.route('**/avatar-probe-unreachable.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="#123456"/></svg>'}))
    await page.evaluate(()=>window.dispatchEvent(new Event('online')))
    await page.locator('#avatar-probe img').evaluate(img=>img.decode())
    await page.evaluate(()=>{window.avatarProbe.state.src='';window.avatarProbe.state.style='invalid'})
    await page.locator('#avatar-probe [data-avatar-state=fallback]').waitFor()
    check((await page.evaluate(()=>window.avatarProbe.failures)).includes('generation'),'Generation failure not observable')
    await page.evaluate(()=>{window.avatarProbe.state.style='gaze';window.avatarProbe.state.decorative=true})
    await page.locator('#avatar-probe img').evaluate(img=>img.decode())
    check(await page.locator('#avatar-probe .fallback-avatar').getAttribute('aria-hidden')==='true','Decorative avatar exposed')
    check(await page.locator('#avatar-probe img').getAttribute('alt')==='','Decorative image has alt')
    await page.evaluate(()=>Object.assign(window.avatarProbe.state,{style:'glyphs',seed:0,src:'',decorative:false}))
    const zero=await svg()
    await page.evaluate(()=>window.avatarProbe.state.seed='0')
    check(await svg()===zero,'Numeric identity zero is unstable')
  } finally {
    await page.context().setOffline(false)
    await page.unroute('**/avatar-probe-unreachable.svg')
    await page.evaluate(theme=>{
      window.avatarProbe?.app.unmount();document.querySelector('#avatar-probe')?.remove()
      window.avatarProbe?.theme.setTheme(theme==='dark')
      delete window.avatarProbe
    },originalTheme)
    page.off('pageerror',errorListener);page.off('request',requestListener)
  }
  check(errors.length===0,errors.join(';'))
  check(external.length===0,'External default-avatar request remains')
  return {localGeneration:true,actualAgentAndUserEntrypoints:true,styles:4,presets:4,modes:2,gazeTransparent:true,themeOverrides:true,offline:true,imageFailureAndRecovery:true,staleErrorIgnored:true,accessibleFallback:true,errors,external}
}
