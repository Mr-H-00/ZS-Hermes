// 在独立 Vite 浏览器会话运行；所有 API 使用合成数据，不提交生成任务。
async page => {
 const origin=page.url().split('/').slice(0,3).join('/');
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let writes=0;
 await page.route('**/api/**',async route=>{
  if(route.request().method()!=='GET'){writes++;return route.fulfill({status:400,json:{detail:'界面检查不提交业务操作'}})}
  const path=route.request().url().split('?')[0];
  let json={data:[],items:[],options:[],models:[],knowledge_bases:[],params:{options:[]}};
  if(path.endsWith('/auth/me'))json={id:1,uid:'audit',username:'界面测试',role:'superadmin'};
  if(path.endsWith('/system/config'))json={};
  if(path.endsWith('/system/config/options'))json={options:[]};
  if(path.endsWith('/departments'))json={departments:[]};
  await route.fulfill({json});
 });
 await page.goto(origin+'/test/browser/settingsStyles.html');
 await page.waitForFunction(()=>window.settingsStyles);
 await page.evaluate(()=>window.settingsStyles.mount('standalone','account'));
 await page.evaluate(async()=>{
  const source=await(await fetch('/src/shared/model/theme.js')).text();
  const dep=name=>source.match(new RegExp(`from "([^"]*/${name}\\.js[^"]*)"`))[1];
  const {createApp,h,reactive,markRaw,nextTick}=await import(dep('vue'));
  const {default:Antd,ConfigProvider}=await import(source.match(/from "([^"]*ant-design-vue\.js[^"]*)"/)[1]);
  const {useThemeStore}=await import('/src/shared/model/theme.js');
  const root=document.querySelector('#app');root.style.display='none';
  const pinia=root.__vue_app__.config.globalProperties.$pinia,theme=useThemeStore(pinia);
  const host=document.createElement('div');document.body.append(host);
  const state=reactive({component:null,props:{}});
  const app=createApp({render:()=>h(ConfigProvider,{theme:theme.currentTheme},{default:()=>state.component?h(state.component,state.props):null})});
  app.use(pinia);app.use(Antd);app.mount(host);
  window.overlayAudit={state,theme,nextTick,async mount(path,props){state.component=null;await nextTick();state.props=props;state.component=markRaw((await import(path)).default);await nextTick()}};
 });
 await page.evaluate(()=>window.overlayAudit.mount('/src/modules/knowledge/ui/BenchmarkGenerateModal.vue',{visible:true,kbId:'audit'}));
 const dialog=page.getByRole('dialog',{name:'自动生成评估基准'});
 await dialog.waitFor();
 /** 验证标题和操作区完整可见，长表单在内部滚动。 */
 const checkBounds=async()=>{
  const layout=await dialog.evaluate(el=>{
   const content=el.querySelector('.ant-modal-content').getBoundingClientRect();
   const footer=el.querySelector('.ant-modal-footer').getBoundingClientRect();
   const body=el.querySelector('.ant-modal-body');body.scrollTop=body.scrollHeight;
   return {top:content.top,bottom:content.bottom,right:content.right,left:content.left,footerBottom:footer.bottom,scrollTop:body.scrollTop,width:innerWidth,height:innerHeight};
  });
  if(layout.top<0||layout.bottom>layout.height||layout.left<0||layout.right>layout.width||layout.footerBottom>layout.height||layout.scrollTop<=0) throw Error('评估基准弹窗边界或内部滚动不正确：'+JSON.stringify(layout));
  return layout;
 };
 const results=[];
 for(const theme of ['light','dark'])for(const width of [1440,1024,768]){
  await page.setViewportSize({width,height:768});await page.evaluate(t=>window.overlayAudit.theme.setTheme(t),theme);
  await page.screenshot({path:`/tmp/yuxi-benchmark-modal-${theme}-${width}.png`,animations:'disabled'});
  results.push({theme,width,...await checkBounds()});
 }
 const regression=await page.addStyleTag({content:'.ant-modal-body {max-height:none !important;overflow:visible !important}'});
 let oldLayoutRejected=false;
 try{await checkBounds()}catch(error){if(!error.message.includes('评估基准弹窗边界'))throw error;oldLayoutRejected=true}
 await regression.evaluate(el=>el.remove());
 await checkBounds();
 if(!oldLayoutRejected)throw Error('未拒绝旧无界表单');
 if(writes||errors.length)throw Error('意外写入或页面错误：'+JSON.stringify({writes,errors}));
 return {results,oldLayoutRejected,writes,errors};
}
