document.addEventListener('DOMContentLoaded', function(){
    const s = 'window.RMMSECTIONCFG.section';
    const loaders = {
        avail:    () => typeof rmmLoadAvailability === 'function' && rmmLoadAvailability(),
        patches:  () => typeof rmmLoadPatches      === 'function' && rmmLoadPatches(),
        software: () => typeof rmmLoadSoftware     === 'function' && rmmLoadSoftware(),
        services: () => typeof rmmLoadServices     === 'function' && rmmLoadServices(),
        events:   () => typeof rmmLoadEvents       === 'function' && rmmLoadEvents(),
        metrics:  () => typeof rmmLoadMetrics      === 'function' && rmmLoadMetrics(),
        hw:       () => typeof rmmLoad             === 'function' && rmmLoad(),
        sec:      () => typeof rmmLoad             === 'function' && rmmLoad(),
        sysinfo:  () => typeof rmmLoad             === 'function' && rmmLoad(),
        transfer: () => typeof rmmLoad             === 'function' && rmmLoad(),
        power:    () => typeof rmmLoad             === 'function' && rmmLoad(),
        scripts:  () => typeof rmmLoad             === 'function' && rmmLoad(),
    };
    if(loaders[s]) setTimeout(()=>loaders[s](), 600);
});
