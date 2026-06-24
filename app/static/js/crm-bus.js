/**
 * Bus de dependencias entre módulos CRM (sin bundler).
 * app.js registra hooks de UI al iniciar.
 */
(function (global) {
    const hooks = {};

    global.CrmBus = {
        on(name, fn) {
            hooks[name] = fn;
        },
        emit(name, ...args) {
            const fn = hooks[name];
            return fn ? fn(...args) : undefined;
        },
        get(name) {
            return hooks[name];
        },
    };
})(window);
