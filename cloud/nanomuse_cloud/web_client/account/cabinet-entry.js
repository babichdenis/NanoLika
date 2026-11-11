/* Public account links enter the current shell. Embedded forms retain their own
   authenticated controller and data. This module never reads or sends credentials. */
(() => {
  const sections = new Set(['overview','usage','api','providers','security','devices','settings']);
  const params = new URLSearchParams(location.search);
  const embedded = params.get('embed') === '1' && window.parent !== window;
  if (!embedded) {
    if (params.get('legacy') === '1') return;
    const next = new URL('/web/cabinet/', location.origin);
    next.searchParams.set('section', sections.has(location.hash.slice(1)) ? location.hash.slice(1) : 'overview');
    if (params.get('source') === 'console') next.searchParams.set('source','console');
    if (params.get('pay') === '1') {
      next.searchParams.set('pay','1');
      for (const name of ['plan','amount']) if(params.has(name)) next.searchParams.set(name,params.get(name));
    }
    location.replace(next.href);
    return;
  }
  document.documentElement.classList.add('cabinet-embedded');
  const notify = type => window.parent.postMessage({type,section:location.hash.slice(1)||'overview'},location.origin);
  window.addEventListener('hashchange',()=>notify('nanolika:account-section'));
  document.addEventListener('DOMContentLoaded',()=> {
    const style = document.createElement('link');style.rel='stylesheet';style.href='/web/account/cabinet-embedded.css?v=20261110';document.head.append(style);
    // External pages must leave the nested document, including the existing sign-in.
    document.addEventListener('click',event=> {
      const link=event.target.closest?.('a[href]');if(!link)return;
      const target=new URL(link.href,location.href);
      if(target.pathname!==location.pathname)link.target='_top';
    });
    const privateContent=document.getElementById('private-content');
    const check=()=>{if(privateContent&&!privateContent.hidden){notify('nanolika:account-ready');return true;}return false;};
    if(!check()&&privateContent)new MutationObserver((_,observer)=>{if(check())observer.disconnect();}).observe(privateContent,{attributes:true,attributeFilter:['hidden']});
  });
})();
