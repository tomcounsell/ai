function _classCallCheck(instance,Constructor){if(!(instance instanceof Constructor)){throw new TypeError("Cannot call a class as a function");}}function _defineProperties(target,props){for(var i=0;i<props.length;i++){var descriptor=props[i];descriptor.enumerable=descriptor.enumerable||false;descriptor.configurable=true;if("value"in descriptor)descriptor.writable=true;Object.defineProperty(target,descriptor.key,descriptor);}}function _createClass(Constructor,protoProps,staticProps){if(protoProps)_defineProperties(Constructor.prototype,protoProps);if(staticProps)_defineProperties(Constructor,staticProps);return Constructor;}function _toConsumableArray(arr){return _arrayWithoutHoles(arr)||_iterableToArray(arr)||_nonIterableSpread();}function _nonIterableSpread(){throw new TypeError("Invalid attempt to spread non-iterable instance");}function _iterableToArray(iter){if(Symbol.iterator in Object(iter)||Object.prototype.toString.call(iter)==="[object Arguments]")return Array.from(iter);}function _arrayWithoutHoles(arr){if(Array.isArray(arr)){for(var i=0,arr2=new Array(arr.length);i<arr.length;i++){arr2[i]=arr[i];}return arr2;}}function ownKeys(object,enumerableOnly){var keys=Object.keys(object);if(Object.getOwnPropertySymbols){var symbols=Object.getOwnPropertySymbols(object);if(enumerableOnly)symbols=symbols.filter(function(sym){return Object.getOwnPropertyDescriptor(object,sym).enumerable;});keys.push.apply(keys,symbols);}return keys;}function _objectSpread(target){for(var i=1;i<arguments.length;i++){var source=arguments[i]!=null?arguments[i]:{};if(i%2){ownKeys(source,true).forEach(function(key){_defineProperty(target,key,source[key]);});}else if(Object.getOwnPropertyDescriptors){Object.defineProperties(target,Object.getOwnPropertyDescriptors(source));}else{ownKeys(source).forEach(function(key){Object.defineProperty(target,key,Object.getOwnPropertyDescriptor(source,key));});}}return target;}function _defineProperty(obj,key,value){if(key in obj){Object.defineProperty(obj,key,{value:value,enumerable:true,configurable:true,writable:true});}else{obj[key]=value;}return obj;}function _typeof2(obj){if(typeof Symbol==="function"&&typeof Symbol.iterator==="symbol"){_typeof2=function _typeof2(obj){return typeof obj;};}else{_typeof2=function _typeof2(obj){return obj&&typeof Symbol==="function"&&obj.constructor===Symbol&&obj!==Symbol.prototype?"symbol":typeof obj;};}return _typeof2(obj);}/* v1.11.1 */this.filestack=this.filestack||{};this.filestack.pick=function(){var ENV={css:{main:'https://static.filestackapi.com/picker/1.11.1/main.css'},vendor:{opentok:'https://static.filestackapi.com/picker/1.11.1/assets/scripts/opentok.js',fabric:'https://static.filestackapi.com/picker/1.11.1/assets/scripts/fabric.js',cropper:'https://static.filestackapi.com/picker/1.11.1/assets/scripts/cropper.js'},sentryDSN:'https://2ff7cfad202f431eb03930e4cc9e5696@sentry.io/210196'};/*!
   * Vue.js v2.6.10
   * (c) 2014-2019 Evan You
   * Released under the MIT License.
   */ /*  */var emptyObject=Object.freeze({});// These helpers produce better VM code in JS engines due to their
// explicitness and function inlining.
function isUndef(v){return v===undefined||v===null;}function isDef(v){return v!==undefined&&v!==null;}function isTrue(v){return v===true;}function isFalse(v){return v===false;}/**
   * Check if value is primitive.
   */function isPrimitive(value){return typeof value==='string'||typeof value==='number'||// $flow-disable-line
_typeof2(value)==='symbol'||typeof value==='boolean';}/**
   * Quick object check - this is primarily used to tell
   * Objects from primitive values when we know the value
   * is a JSON-compliant type.
   */function isObject(obj){return obj!==null&&_typeof2(obj)==='object';}/**
   * Get the raw type string of a value, e.g., [object Object].
   */var _toString=Object.prototype.toString;function toRawType(value){return _toString.call(value).slice(8,-1);}/**
   * Strict object type check. Only returns true
   * for plain JavaScript objects.
   */function isPlainObject(obj){return _toString.call(obj)==='[object Object]';}function isRegExp(v){return _toString.call(v)==='[object RegExp]';}/**
   * Check if val is a valid array index.
   */function isValidArrayIndex(val){var n=parseFloat(String(val));return n>=0&&Math.floor(n)===n&&isFinite(val);}function isPromise(val){return isDef(val)&&typeof val.then==='function'&&typeof val["catch"]==='function';}/**
   * Convert a value to a string that is actually rendered.
   */function toString(val){return val==null?'':Array.isArray(val)||isPlainObject(val)&&val.toString===_toString?JSON.stringify(val,null,2):String(val);}/**
   * Convert an input value to a number for persistence.
   * If the conversion fails, return original string.
   */function toNumber(val){var n=parseFloat(val);return isNaN(n)?val:n;}/**
   * Make a map and return a function for checking if a key
   * is in that map.
   */function makeMap(str,expectsLowerCase){var map=Object.create(null);var list=str.split(',');for(var i=0;i<list.length;i++){map[list[i]]=true;}return expectsLowerCase?function(val){return map[val.toLowerCase()];}:function(val){return map[val];};}/**
   * Check if a tag is a built-in tag.
   */var isBuiltInTag=makeMap('slot,component',true);/**
   * Check if an attribute is a reserved attribute.
   */var isReservedAttribute=makeMap('key,ref,slot,slot-scope,is');/**
   * Remove an item from an array.
   */function remove(arr,item){if(arr.length){var index=arr.indexOf(item);if(index>-1){return arr.splice(index,1);}}}/**
   * Check whether an object has the property.
   */var hasOwnProperty=Object.prototype.hasOwnProperty;function hasOwn(obj,key){return hasOwnProperty.call(obj,key);}/**
   * Create a cached version of a pure function.
   */function cached(fn){var cache=Object.create(null);return function cachedFn(str){var hit=cache[str];return hit||(cache[str]=fn(str));};}/**
   * Camelize a hyphen-delimited string.
   */var camelizeRE=/-(\w)/g;var camelize=cached(function(str){return str.replace(camelizeRE,function(_,c){return c?c.toUpperCase():'';});});/**
   * Capitalize a string.
   */var capitalize=cached(function(str){return str.charAt(0).toUpperCase()+str.slice(1);});/**
   * Hyphenate a camelCase string.
   */var hyphenateRE=/\B([A-Z])/g;var hyphenate=cached(function(str){return str.replace(hyphenateRE,'-$1').toLowerCase();});/**
   * Simple bind polyfill for environments that do not support it,
   * e.g., PhantomJS 1.x. Technically, we don't need this anymore
   * since native bind is now performant enough in most browsers.
   * But removing it would mean breaking code that was able to run in
   * PhantomJS 1.x, so this must be kept for backward compatibility.
   */ /* istanbul ignore next */function polyfillBind(fn,ctx){function boundFn(a){var l=arguments.length;return l?l>1?fn.apply(ctx,arguments):fn.call(ctx,a):fn.call(ctx);}boundFn._length=fn.length;return boundFn;}function nativeBind(fn,ctx){return fn.bind(ctx);}var bind=Function.prototype.bind?nativeBind:polyfillBind;/**
   * Convert an Array-like object to a real Array.
   */function toArray(list,start){start=start||0;var i=list.length-start;var ret=new Array(i);while(i--){ret[i]=list[i+start];}return ret;}/**
   * Mix properties into target object.
   */function extend(to,_from){for(var key in _from){to[key]=_from[key];}return to;}/**
   * Merge an Array of Objects into a single Object.
   */function toObject(arr){var res={};for(var i=0;i<arr.length;i++){if(arr[i]){extend(res,arr[i]);}}return res;}/* eslint-disable no-unused-vars */ /**
   * Perform no operation.
   * Stubbing args to make Flow happy without leaving useless transpiled code
   * with ...rest (https://flow.org/blog/2017/05/07/Strict-Function-Call-Arity/).
   */function noop(a,b,c){}/**
   * Always return false.
   */var no=function no(a,b,c){return false;};/* eslint-enable no-unused-vars */ /**
   * Return the same value.
   */var identity=function identity(_){return _;};/**
   * Check if two values are loosely equal - that is,
   * if they are plain objects, do they have the same shape?
   */function looseEqual(a,b){if(a===b){return true;}var isObjectA=isObject(a);var isObjectB=isObject(b);if(isObjectA&&isObjectB){try{var isArrayA=Array.isArray(a);var isArrayB=Array.isArray(b);if(isArrayA&&isArrayB){return a.length===b.length&&a.every(function(e,i){return looseEqual(e,b[i]);});}else if(a instanceof Date&&b instanceof Date){return a.getTime()===b.getTime();}else if(!isArrayA&&!isArrayB){var keysA=Object.keys(a);var keysB=Object.keys(b);return keysA.length===keysB.length&&keysA.every(function(key){return looseEqual(a[key],b[key]);});}else{/* istanbul ignore next */return false;}}catch(e){/* istanbul ignore next */return false;}}else if(!isObjectA&&!isObjectB){return String(a)===String(b);}else{return false;}}/**
   * Return the first index at which a loosely equal value can be
   * found in the array (if value is a plain object, the array must
   * contain an object of the same shape), or -1 if it is not present.
   */function looseIndexOf(arr,val){for(var i=0;i<arr.length;i++){if(looseEqual(arr[i],val)){return i;}}return-1;}/**
   * Ensure a function is called only once.
   */function once(fn){var called=false;return function(){if(!called){called=true;fn.apply(this,arguments);}};}var SSR_ATTR='data-server-rendered';var ASSET_TYPES=['component','directive','filter'];var LIFECYCLE_HOOKS=['beforeCreate','created','beforeMount','mounted','beforeUpdate','updated','beforeDestroy','destroyed','activated','deactivated','errorCaptured','serverPrefetch'];/*  */var config={/**
     * Option merge strategies (used in core/util/options)
     */ // $flow-disable-line
optionMergeStrategies:Object.create(null),/**
     * Whether to suppress warnings.
     */silent:false,/**
     * Show production mode tip message on boot?
     */productionTip:"production"!=='production',/**
     * Whether to enable devtools
     */devtools:"production"!=='production',/**
     * Whether to record perf
     */performance:false,/**
     * Error handler for watcher errors
     */errorHandler:null,/**
     * Warn handler for watcher warns
     */warnHandler:null,/**
     * Ignore certain custom elements
     */ignoredElements:[],/**
     * Custom user key aliases for v-on
     */ // $flow-disable-line
keyCodes:Object.create(null),/**
     * Check if a tag is reserved so that it cannot be registered as a
     * component. This is platform-dependent and may be overwritten.
     */isReservedTag:no,/**
     * Check if an attribute is reserved so that it cannot be used as a component
     * prop. This is platform-dependent and may be overwritten.
     */isReservedAttr:no,/**
     * Check if a tag is an unknown element.
     * Platform-dependent.
     */isUnknownElement:no,/**
     * Get the namespace of an element
     */getTagNamespace:noop,/**
     * Parse the real tag name for the specific platform.
     */parsePlatformTagName:identity,/**
     * Check if an attribute must be bound using property, e.g. value
     * Platform-dependent.
     */mustUseProp:no,/**
     * Perform updates asynchronously. Intended to be used by Vue Test Utils
     * This will significantly reduce performance if set to false.
     */async:true,/**
     * Exposed for legacy reasons
     */_lifecycleHooks:LIFECYCLE_HOOKS};/*  */ /**
   * unicode letters used for parsing html tags, component names and property paths.
   * using https://www.w3.org/TR/html53/semantics-scripting.html#potentialcustomelementname
   * skipping \u10000-\uEFFFF due to it freezing up PhantomJS
   */var unicodeRegExp=/a-zA-Z\u00B7\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u037D\u037F-\u1FFF\u200C-\u200D\u203F-\u2040\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD/;/**
   * Check if a string starts with $ or _
   */function isReserved(str){var c=(str+'').charCodeAt(0);return c===0x24||c===0x5F;}/**
   * Define a property.
   */function def(obj,key,val,enumerable){Object.defineProperty(obj,key,{value:val,enumerable:!!enumerable,writable:true,configurable:true});}/**
   * Parse simple path.
   */var bailRE=new RegExp("[^"+unicodeRegExp.source+".$_\\d]");function parsePath(path){if(bailRE.test(path)){return;}var segments=path.split('.');return function(obj){for(var i=0;i<segments.length;i++){if(!obj){return;}obj=obj[segments[i]];}return obj;};}/*  */ // can we use __proto__?
var hasProto='__proto__'in{};// Browser environment sniffing
var inBrowser=typeof window!=='undefined';var inWeex=typeof WXEnvironment!=='undefined'&&!!WXEnvironment.platform;var weexPlatform=inWeex&&WXEnvironment.platform.toLowerCase();var UA=inBrowser&&window.navigator.userAgent.toLowerCase();var isIE=UA&&/msie|trident/.test(UA);var isIE9=UA&&UA.indexOf('msie 9.0')>0;var isEdge=UA&&UA.indexOf('edge/')>0;var isAndroid=UA&&UA.indexOf('android')>0||weexPlatform==='android';var isIOS=UA&&/iphone|ipad|ipod|ios/.test(UA)||weexPlatform==='ios';var isChrome=UA&&/chrome\/\d+/.test(UA)&&!isEdge;var isPhantomJS=UA&&/phantomjs/.test(UA);var isFF=UA&&UA.match(/firefox\/(\d+)/);// Firefox has a "watch" function on Object.prototype...
var nativeWatch={}.watch;var supportsPassive=false;if(inBrowser){try{var opts={};Object.defineProperty(opts,'passive',{get:function get(){/* istanbul ignore next */supportsPassive=true;}});// https://github.com/facebook/flow/issues/285
window.addEventListener('test-passive',null,opts);}catch(e){}}// this needs to be lazy-evaled because vue may be required before
// vue-server-renderer can set VUE_ENV
var _isServer;var isServerRendering=function isServerRendering(){if(_isServer===undefined){/* istanbul ignore if */if(!inBrowser&&!inWeex&&typeof global!=='undefined'){// detect presence of vue-server-renderer and avoid
// Webpack shimming the process
_isServer=global['process']&&global['process'].env.VUE_ENV==='server';}else{_isServer=false;}}return _isServer;};// detect devtools
var devtools=inBrowser&&window.__VUE_DEVTOOLS_GLOBAL_HOOK__;/* istanbul ignore next */function isNative(Ctor){return typeof Ctor==='function'&&/native code/.test(Ctor.toString());}var hasSymbol=typeof Symbol!=='undefined'&&isNative(Symbol)&&typeof Reflect!=='undefined'&&isNative(Reflect.ownKeys);var _Set;/* istanbul ignore if */ // $flow-disable-line
if(typeof Set!=='undefined'&&isNative(Set)){// use native Set when available.
_Set=Set;}else{// a non-standard Set polyfill that only works with primitive keys.
_Set=/*@__PURE__*/function(){function Set(){this.set=Object.create(null);}Set.prototype.has=function has(key){return this.set[key]===true;};Set.prototype.add=function add(key){this.set[key]=true;};Set.prototype.clear=function clear(){this.set=Object.create(null);};return Set;}();}/*  */var warn=noop;/*  */var uid=0;/**
   * A dep is an observable that can have multiple
   * directives subscribing to it.
   */var Dep=function Dep(){this.id=uid++;this.subs=[];};Dep.prototype.addSub=function addSub(sub){this.subs.push(sub);};Dep.prototype.removeSub=function removeSub(sub){remove(this.subs,sub);};Dep.prototype.depend=function depend(){if(Dep.target){Dep.target.addDep(this);}};Dep.prototype.notify=function notify(){// stabilize the subscriber list first
var subs=this.subs.slice();for(var i=0,l=subs.length;i<l;i++){subs[i].update();}};// The current target watcher being evaluated.
// This is globally unique because only one watcher
// can be evaluated at a time.
Dep.target=null;var targetStack=[];function pushTarget(target){targetStack.push(target);Dep.target=target;}function popTarget(){targetStack.pop();Dep.target=targetStack[targetStack.length-1];}/*  */var VNode=function VNode(tag,data,children,text,elm,context,componentOptions,asyncFactory){this.tag=tag;this.data=data;this.children=children;this.text=text;this.elm=elm;this.ns=undefined;this.context=context;this.fnContext=undefined;this.fnOptions=undefined;this.fnScopeId=undefined;this.key=data&&data.key;this.componentOptions=componentOptions;this.componentInstance=undefined;this.parent=undefined;this.raw=false;this.isStatic=false;this.isRootInsert=true;this.isComment=false;this.isCloned=false;this.isOnce=false;this.asyncFactory=asyncFactory;this.asyncMeta=undefined;this.isAsyncPlaceholder=false;};var prototypeAccessors={child:{configurable:true}};// DEPRECATED: alias for componentInstance for backwards compat.
/* istanbul ignore next */prototypeAccessors.child.get=function(){return this.componentInstance;};Object.defineProperties(VNode.prototype,prototypeAccessors);var createEmptyVNode=function createEmptyVNode(text){if(text===void 0)text='';var node=new VNode();node.text=text;node.isComment=true;return node;};function createTextVNode(val){return new VNode(undefined,undefined,undefined,String(val));}// optimized shallow clone
// used for static nodes and slot nodes because they may be reused across
// multiple renders, cloning them avoids errors when DOM manipulations rely
// on their elm reference.
function cloneVNode(vnode){var cloned=new VNode(vnode.tag,vnode.data,// #7975
// clone children array to avoid mutating original in case of cloning
// a child.
vnode.children&&vnode.children.slice(),vnode.text,vnode.elm,vnode.context,vnode.componentOptions,vnode.asyncFactory);cloned.ns=vnode.ns;cloned.isStatic=vnode.isStatic;cloned.key=vnode.key;cloned.isComment=vnode.isComment;cloned.fnContext=vnode.fnContext;cloned.fnOptions=vnode.fnOptions;cloned.fnScopeId=vnode.fnScopeId;cloned.asyncMeta=vnode.asyncMeta;cloned.isCloned=true;return cloned;}/*
   * not type checking this file because flow doesn't play well with
   * dynamically accessing methods on Array prototype
   */var arrayProto=Array.prototype;var arrayMethods=Object.create(arrayProto);var methodsToPatch=['push','pop','shift','unshift','splice','sort','reverse'];/**
   * Intercept mutating methods and emit events
   */methodsToPatch.forEach(function(method){// cache original method
var original=arrayProto[method];def(arrayMethods,method,function mutator(){var args=[],len=arguments.length;while(len--){args[len]=arguments[len];}var result=original.apply(this,args);var ob=this.__ob__;var inserted;switch(method){case'push':case'unshift':inserted=args;break;case'splice':inserted=args.slice(2);break;}if(inserted){ob.observeArray(inserted);}// notify change
ob.dep.notify();return result;});});/*  */var arrayKeys=Object.getOwnPropertyNames(arrayMethods);/**
   * In some cases we may want to disable observation inside a component's
   * update computation.
   */var shouldObserve=true;function toggleObserving(value){shouldObserve=value;}/**
   * Observer class that is attached to each observed
   * object. Once attached, the observer converts the target
   * object's property keys into getter/setters that
   * collect dependencies and dispatch updates.
   */var Observer=function Observer(value){this.value=value;this.dep=new Dep();this.vmCount=0;def(value,'__ob__',this);if(Array.isArray(value)){if(hasProto){protoAugment(value,arrayMethods);}else{copyAugment(value,arrayMethods,arrayKeys);}this.observeArray(value);}else{this.walk(value);}};/**
   * Walk through all properties and convert them into
   * getter/setters. This method should only be called when
   * value type is Object.
   */Observer.prototype.walk=function walk(obj){var keys=Object.keys(obj);for(var i=0;i<keys.length;i++){defineReactive$$1(obj,keys[i]);}};/**
   * Observe a list of Array items.
   */Observer.prototype.observeArray=function observeArray(items){for(var i=0,l=items.length;i<l;i++){observe(items[i]);}};// helpers
/**
   * Augment a target Object or Array by intercepting
   * the prototype chain using __proto__
   */function protoAugment(target,src){/* eslint-disable no-proto */target.__proto__=src;/* eslint-enable no-proto */}/**
   * Augment a target Object or Array by defining
   * hidden properties.
   */ /* istanbul ignore next */function copyAugment(target,src,keys){for(var i=0,l=keys.length;i<l;i++){var key=keys[i];def(target,key,src[key]);}}/**
   * Attempt to create an observer instance for a value,
   * returns the new observer if successfully observed,
   * or the existing observer if the value already has one.
   */function observe(value,asRootData){if(!isObject(value)||value instanceof VNode){return;}var ob;if(hasOwn(value,'__ob__')&&value.__ob__ instanceof Observer){ob=value.__ob__;}else if(shouldObserve&&!isServerRendering()&&(Array.isArray(value)||isPlainObject(value))&&Object.isExtensible(value)&&!value._isVue){ob=new Observer(value);}if(asRootData&&ob){ob.vmCount++;}return ob;}/**
   * Define a reactive property on an Object.
   */function defineReactive$$1(obj,key,val,customSetter,shallow){var dep=new Dep();var property=Object.getOwnPropertyDescriptor(obj,key);if(property&&property.configurable===false){return;}// cater for pre-defined getter/setters
var getter=property&&property.get;var setter=property&&property.set;if((!getter||setter)&&arguments.length===2){val=obj[key];}var childOb=!shallow&&observe(val);Object.defineProperty(obj,key,{enumerable:true,configurable:true,get:function reactiveGetter(){var value=getter?getter.call(obj):val;if(Dep.target){dep.depend();if(childOb){childOb.dep.depend();if(Array.isArray(value)){dependArray(value);}}}return value;},set:function reactiveSetter(newVal){var value=getter?getter.call(obj):val;/* eslint-disable no-self-compare */if(newVal===value||newVal!==newVal&&value!==value){return;}// #7981: for accessor properties without setter
if(getter&&!setter){return;}if(setter){setter.call(obj,newVal);}else{val=newVal;}childOb=!shallow&&observe(newVal);dep.notify();}});}/**
   * Set a property on an object. Adds the new property and
   * triggers change notification if the property doesn't
   * already exist.
   */function set(target,key,val){if(Array.isArray(target)&&isValidArrayIndex(key)){target.length=Math.max(target.length,key);target.splice(key,1,val);return val;}if(key in target&&!(key in Object.prototype)){target[key]=val;return val;}var ob=target.__ob__;if(target._isVue||ob&&ob.vmCount){return val;}if(!ob){target[key]=val;return val;}defineReactive$$1(ob.value,key,val);ob.dep.notify();return val;}/**
   * Delete a property and trigger change if necessary.
   */function del(target,key){if(Array.isArray(target)&&isValidArrayIndex(key)){target.splice(key,1);return;}var ob=target.__ob__;if(target._isVue||ob&&ob.vmCount){return;}if(!hasOwn(target,key)){return;}delete target[key];if(!ob){return;}ob.dep.notify();}/**
   * Collect dependencies on array elements when the array is touched, since
   * we cannot intercept array element access like property getters.
   */function dependArray(value){for(var e=void 0,i=0,l=value.length;i<l;i++){e=value[i];e&&e.__ob__&&e.__ob__.dep.depend();if(Array.isArray(e)){dependArray(e);}}}/*  */ /**
   * Option overwriting strategies are functions that handle
   * how to merge a parent option value and a child option
   * value into the final value.
   */var strats=config.optionMergeStrategies;/**
   * Helper that recursively merges two data objects together.
   */function mergeData(to,from){if(!from){return to;}var key,toVal,fromVal;var keys=hasSymbol?Reflect.ownKeys(from):Object.keys(from);for(var i=0;i<keys.length;i++){key=keys[i];// in case the object is already observed...
if(key==='__ob__'){continue;}toVal=to[key];fromVal=from[key];if(!hasOwn(to,key)){set(to,key,fromVal);}else if(toVal!==fromVal&&isPlainObject(toVal)&&isPlainObject(fromVal)){mergeData(toVal,fromVal);}}return to;}/**
   * Data
   */function mergeDataOrFn(parentVal,childVal,vm){if(!vm){// in a Vue.extend merge, both should be functions
if(!childVal){return parentVal;}if(!parentVal){return childVal;}// when parentVal & childVal are both present,
// we need to return a function that returns the
// merged result of both functions... no need to
// check if parentVal is a function here because
// it has to be a function to pass previous merges.
return function mergedDataFn(){return mergeData(typeof childVal==='function'?childVal.call(this,this):childVal,typeof parentVal==='function'?parentVal.call(this,this):parentVal);};}else{return function mergedInstanceDataFn(){// instance merge
var instanceData=typeof childVal==='function'?childVal.call(vm,vm):childVal;var defaultData=typeof parentVal==='function'?parentVal.call(vm,vm):parentVal;if(instanceData){return mergeData(instanceData,defaultData);}else{return defaultData;}};}}strats.data=function(parentVal,childVal,vm){if(!vm){if(childVal&&typeof childVal!=='function'){return parentVal;}return mergeDataOrFn(parentVal,childVal);}return mergeDataOrFn(parentVal,childVal,vm);};/**
   * Hooks and props are merged as arrays.
   */function mergeHook(parentVal,childVal){var res=childVal?parentVal?parentVal.concat(childVal):Array.isArray(childVal)?childVal:[childVal]:parentVal;return res?dedupeHooks(res):res;}function dedupeHooks(hooks){var res=[];for(var i=0;i<hooks.length;i++){if(res.indexOf(hooks[i])===-1){res.push(hooks[i]);}}return res;}LIFECYCLE_HOOKS.forEach(function(hook){strats[hook]=mergeHook;});/**
   * Assets
   *
   * When a vm is present (instance creation), we need to do
   * a three-way merge between constructor options, instance
   * options and parent options.
   */function mergeAssets(parentVal,childVal,vm,key){var res=Object.create(parentVal||null);if(childVal){return extend(res,childVal);}else{return res;}}ASSET_TYPES.forEach(function(type){strats[type+'s']=mergeAssets;});/**
   * Watchers.
   *
   * Watchers hashes should not overwrite one
   * another, so we merge them as arrays.
   */strats.watch=function(parentVal,childVal,vm,key){// work around Firefox's Object.prototype.watch...
if(parentVal===nativeWatch){parentVal=undefined;}if(childVal===nativeWatch){childVal=undefined;}/* istanbul ignore if */if(!childVal){return Object.create(parentVal||null);}if(!parentVal){return childVal;}var ret={};extend(ret,parentVal);for(var key$1 in childVal){var parent=ret[key$1];var child=childVal[key$1];if(parent&&!Array.isArray(parent)){parent=[parent];}ret[key$1]=parent?parent.concat(child):Array.isArray(child)?child:[child];}return ret;};/**
   * Other object hashes.
   */strats.props=strats.methods=strats.inject=strats.computed=function(parentVal,childVal,vm,key){if(childVal&&"production"!=='production'){assertObjectType(key,childVal,vm);}if(!parentVal){return childVal;}var ret=Object.create(null);extend(ret,parentVal);if(childVal){extend(ret,childVal);}return ret;};strats.provide=mergeDataOrFn;/**
   * Default strategy.
   */var defaultStrat=function defaultStrat(parentVal,childVal){return childVal===undefined?parentVal:childVal;};/**
   * Ensure all props option syntax are normalized into the
   * Object-based format.
   */function normalizeProps(options,vm){var props=options.props;if(!props){return;}var res={};var i,val,name;if(Array.isArray(props)){i=props.length;while(i--){val=props[i];if(typeof val==='string'){name=camelize(val);res[name]={type:null};}}}else if(isPlainObject(props)){for(var key in props){val=props[key];name=camelize(key);res[name]=isPlainObject(val)?val:{type:val};}}options.props=res;}/**
   * Normalize all injections into Object-based format
   */function normalizeInject(options,vm){var inject=options.inject;if(!inject){return;}var normalized=options.inject={};if(Array.isArray(inject)){for(var i=0;i<inject.length;i++){normalized[inject[i]]={from:inject[i]};}}else if(isPlainObject(inject)){for(var key in inject){var val=inject[key];normalized[key]=isPlainObject(val)?extend({from:key},val):{from:val};}}}/**
   * Normalize raw function directives into object format.
   */function normalizeDirectives(options){var dirs=options.directives;if(dirs){for(var key in dirs){var def$$1=dirs[key];if(typeof def$$1==='function'){dirs[key]={bind:def$$1,update:def$$1};}}}}function assertObjectType(name,value,vm){if(!isPlainObject(value)){warn("Invalid value for option \""+name+"\": expected an Object, "+"but got "+toRawType(value)+".",vm);}}/**
   * Merge two option objects into a new one.
   * Core utility used in both instantiation and inheritance.
   */function mergeOptions(parent,child,vm){if(typeof child==='function'){child=child.options;}normalizeProps(child);normalizeInject(child);normalizeDirectives(child);// Apply extends and mixins on the child options,
// but only if it is a raw options object that isn't
// the result of another mergeOptions call.
// Only merged options has the _base property.
if(!child._base){if(child["extends"]){parent=mergeOptions(parent,child["extends"],vm);}if(child.mixins){for(var i=0,l=child.mixins.length;i<l;i++){parent=mergeOptions(parent,child.mixins[i],vm);}}}var options={};var key;for(key in parent){mergeField(key);}for(key in child){if(!hasOwn(parent,key)){mergeField(key);}}function mergeField(key){var strat=strats[key]||defaultStrat;options[key]=strat(parent[key],child[key],vm,key);}return options;}/**
   * Resolve an asset.
   * This function is used because child instances need access
   * to assets defined in its ancestor chain.
   */function resolveAsset(options,type,id,warnMissing){/* istanbul ignore if */if(typeof id!=='string'){return;}var assets=options[type];// check local registration variations first
if(hasOwn(assets,id)){return assets[id];}var camelizedId=camelize(id);if(hasOwn(assets,camelizedId)){return assets[camelizedId];}var PascalCaseId=capitalize(camelizedId);if(hasOwn(assets,PascalCaseId)){return assets[PascalCaseId];}// fallback to prototype chain
var res=assets[id]||assets[camelizedId]||assets[PascalCaseId];return res;}/*  */function validateProp(key,propOptions,propsData,vm){var prop=propOptions[key];var absent=!hasOwn(propsData,key);var value=propsData[key];// boolean casting
var booleanIndex=getTypeIndex(Boolean,prop.type);if(booleanIndex>-1){if(absent&&!hasOwn(prop,'default')){value=false;}else if(value===''||value===hyphenate(key)){// only cast empty string / same name to boolean if
// boolean has higher priority
var stringIndex=getTypeIndex(String,prop.type);if(stringIndex<0||booleanIndex<stringIndex){value=true;}}}// check default value
if(value===undefined){value=getPropDefaultValue(vm,prop,key);// since the default value is a fresh copy,
// make sure to observe it.
var prevShouldObserve=shouldObserve;toggleObserving(true);observe(value);toggleObserving(prevShouldObserve);}return value;}/**
   * Get the default value of a prop.
   */function getPropDefaultValue(vm,prop,key){// no default, return undefined
if(!hasOwn(prop,'default')){return undefined;}var def=prop["default"];// the raw prop value was also undefined from previous render,
// return previous default value to avoid unnecessary watcher trigger
if(vm&&vm.$options.propsData&&vm.$options.propsData[key]===undefined&&vm._props[key]!==undefined){return vm._props[key];}// call factory function for non-Function types
// a value is Function if its prototype is function even across different execution context
return typeof def==='function'&&getType(prop.type)!=='Function'?def.call(vm):def;}/**
   * Use function string name to check built-in types,
   * because a simple equality check will fail when running
   * across different vms / iframes.
   */function getType(fn){var match=fn&&fn.toString().match(/^\s*function (\w+)/);return match?match[1]:'';}function isSameType(a,b){return getType(a)===getType(b);}function getTypeIndex(type,expectedTypes){if(!Array.isArray(expectedTypes)){return isSameType(expectedTypes,type)?0:-1;}for(var i=0,len=expectedTypes.length;i<len;i++){if(isSameType(expectedTypes[i],type)){return i;}}return-1;}/*  */function handleError(err,vm,info){// Deactivate deps tracking while processing error handler to avoid possible infinite rendering.
// See: https://github.com/vuejs/vuex/issues/1505
pushTarget();try{if(vm){var cur=vm;while(cur=cur.$parent){var hooks=cur.$options.errorCaptured;if(hooks){for(var i=0;i<hooks.length;i++){try{var capture=hooks[i].call(cur,err,vm,info)===false;if(capture){return;}}catch(e){globalHandleError(e,cur,'errorCaptured hook');}}}}}globalHandleError(err,vm,info);}finally{popTarget();}}function invokeWithErrorHandling(handler,context,args,vm,info){var res;try{res=args?handler.apply(context,args):handler.call(context);if(res&&!res._isVue&&isPromise(res)&&!res._handled){res["catch"](function(e){return handleError(e,vm,info+" (Promise/async)");});// issue #9511
// avoid catch triggering multiple times when nested calls
res._handled=true;}}catch(e){handleError(e,vm,info);}return res;}function globalHandleError(err,vm,info){if(config.errorHandler){try{return config.errorHandler.call(null,err,vm,info);}catch(e){// if the user intentionally throws the original error in the handler,
// do not log it twice
if(e!==err){logError(e);}}}logError(err);}function logError(err,vm,info){/* istanbul ignore else */if((inBrowser||inWeex)&&typeof console!=='undefined'){console.error(err);}else{throw err;}}/*  */var isUsingMicroTask=false;var callbacks=[];var pending=false;function flushCallbacks(){pending=false;var copies=callbacks.slice(0);callbacks.length=0;for(var i=0;i<copies.length;i++){copies[i]();}}// Here we have async deferring wrappers using microtasks.
// In 2.5 we used (macro) tasks (in combination with microtasks).
// However, it has subtle problems when state is changed right before repaint
// (e.g. #6813, out-in transitions).
// Also, using (macro) tasks in event handler would cause some weird behaviors
// that cannot be circumvented (e.g. #7109, #7153, #7546, #7834, #8109).
// So we now use microtasks everywhere, again.
// A major drawback of this tradeoff is that there are some scenarios
// where microtasks have too high a priority and fire in between supposedly
// sequential events (e.g. #4521, #6690, which have workarounds)
// or even between bubbling of the same event (#6566).
var timerFunc;// The nextTick behavior leverages the microtask queue, which can be accessed
// via either native Promise.then or MutationObserver.
// MutationObserver has wider support, however it is seriously bugged in
// UIWebView in iOS >= 9.3.3 when triggered in touch event handlers. It
// completely stops working after triggering a few times... so, if native
// Promise is available, we will use it:
/* istanbul ignore next, $flow-disable-line */if(typeof Promise!=='undefined'&&isNative(Promise)){var p=Promise.resolve();timerFunc=function timerFunc(){p.then(flushCallbacks);// In problematic UIWebViews, Promise.then doesn't completely break, but
// it can get stuck in a weird state where callbacks are pushed into the
// microtask queue but the queue isn't being flushed, until the browser
// needs to do some other work, e.g. handle a timer. Therefore we can
// "force" the microtask queue to be flushed by adding an empty timer.
if(isIOS){setTimeout(noop);}};isUsingMicroTask=true;}else if(!isIE&&typeof MutationObserver!=='undefined'&&(isNative(MutationObserver)||// PhantomJS and iOS 7.x
MutationObserver.toString()==='[object MutationObserverConstructor]')){// Use MutationObserver where native Promise is not available,
// e.g. PhantomJS, iOS7, Android 4.4
// (#6466 MutationObserver is unreliable in IE11)
var counter=1;var observer=new MutationObserver(flushCallbacks);var textNode=document.createTextNode(String(counter));observer.observe(textNode,{characterData:true});timerFunc=function timerFunc(){counter=(counter+1)%2;textNode.data=String(counter);};isUsingMicroTask=true;}else if(typeof setImmediate!=='undefined'&&isNative(setImmediate)){// Fallback to setImmediate.
// Techinically it leverages the (macro) task queue,
// but it is still a better choice than setTimeout.
timerFunc=function timerFunc(){setImmediate(flushCallbacks);};}else{// Fallback to setTimeout.
timerFunc=function timerFunc(){setTimeout(flushCallbacks,0);};}function nextTick(cb,ctx){var _resolve;callbacks.push(function(){if(cb){try{cb.call(ctx);}catch(e){handleError(e,ctx,'nextTick');}}else if(_resolve){_resolve(ctx);}});if(!pending){pending=true;timerFunc();}// $flow-disable-line
if(!cb&&typeof Promise!=='undefined'){return new Promise(function(resolve){_resolve=resolve;});}}/*  */var seenObjects=new _Set();/**
   * Recursively traverse an object to evoke all converted
   * getters, so that every nested property inside the object
   * is collected as a "deep" dependency.
   */function traverse(val){_traverse(val,seenObjects);seenObjects.clear();}function _traverse(val,seen){var i,keys;var isA=Array.isArray(val);if(!isA&&!isObject(val)||Object.isFrozen(val)||val instanceof VNode){return;}if(val.__ob__){var depId=val.__ob__.dep.id;if(seen.has(depId)){return;}seen.add(depId);}if(isA){i=val.length;while(i--){_traverse(val[i],seen);}}else{keys=Object.keys(val);i=keys.length;while(i--){_traverse(val[keys[i]],seen);}}}/*  */var normalizeEvent=cached(function(name){var passive=name.charAt(0)==='&';name=passive?name.slice(1):name;var once$$1=name.charAt(0)==='~';// Prefixed last, checked first
name=once$$1?name.slice(1):name;var capture=name.charAt(0)==='!';name=capture?name.slice(1):name;return{name:name,once:once$$1,capture:capture,passive:passive};});function createFnInvoker(fns,vm){function invoker(){var arguments$1=arguments;var fns=invoker.fns;if(Array.isArray(fns)){var cloned=fns.slice();for(var i=0;i<cloned.length;i++){invokeWithErrorHandling(cloned[i],null,arguments$1,vm,"v-on handler");}}else{// return handler return value for single handlers
return invokeWithErrorHandling(fns,null,arguments,vm,"v-on handler");}}invoker.fns=fns;return invoker;}function updateListeners(on,oldOn,add,remove$$1,createOnceHandler,vm){var name,def$$1,cur,old,event;for(name in on){def$$1=cur=on[name];old=oldOn[name];event=normalizeEvent(name);if(isUndef(cur));else if(isUndef(old)){if(isUndef(cur.fns)){cur=on[name]=createFnInvoker(cur,vm);}if(isTrue(event.once)){cur=on[name]=createOnceHandler(event.name,cur,event.capture);}add(event.name,cur,event.capture,event.passive,event.params);}else if(cur!==old){old.fns=cur;on[name]=old;}}for(name in oldOn){if(isUndef(on[name])){event=normalizeEvent(name);remove$$1(event.name,oldOn[name],event.capture);}}}/*  */function mergeVNodeHook(def,hookKey,hook){if(def instanceof VNode){def=def.data.hook||(def.data.hook={});}var invoker;var oldHook=def[hookKey];function wrappedHook(){hook.apply(this,arguments);// important: remove merged hook to ensure it's called only once
// and prevent memory leak
remove(invoker.fns,wrappedHook);}if(isUndef(oldHook)){// no existing hook
invoker=createFnInvoker([wrappedHook]);}else{/* istanbul ignore if */if(isDef(oldHook.fns)&&isTrue(oldHook.merged)){// already a merged invoker
invoker=oldHook;invoker.fns.push(wrappedHook);}else{// existing plain hook
invoker=createFnInvoker([oldHook,wrappedHook]);}}invoker.merged=true;def[hookKey]=invoker;}/*  */function extractPropsFromVNodeData(data,Ctor,tag){// we are only extracting raw values here.
// validation and default values are handled in the child
// component itself.
var propOptions=Ctor.options.props;if(isUndef(propOptions)){return;}var res={};var attrs=data.attrs;var props=data.props;if(isDef(attrs)||isDef(props)){for(var key in propOptions){var altKey=hyphenate(key);checkProp(res,props,key,altKey,true)||checkProp(res,attrs,key,altKey,false);}}return res;}function checkProp(res,hash,key,altKey,preserve){if(isDef(hash)){if(hasOwn(hash,key)){res[key]=hash[key];if(!preserve){delete hash[key];}return true;}else if(hasOwn(hash,altKey)){res[key]=hash[altKey];if(!preserve){delete hash[altKey];}return true;}}return false;}/*  */ // The template compiler attempts to minimize the need for normalization by
// statically analyzing the template at compile time.
//
// For plain HTML markup, normalization can be completely skipped because the
// generated render function is guaranteed to return Array<VNode>. There are
// two cases where extra normalization is needed:
// 1. When the children contains components - because a functional component
// may return an Array instead of a single root. In this case, just a simple
// normalization is needed - if any child is an Array, we flatten the whole
// thing with Array.prototype.concat. It is guaranteed to be only 1-level deep
// because functional components already normalize their own children.
function simpleNormalizeChildren(children){for(var i=0;i<children.length;i++){if(Array.isArray(children[i])){return Array.prototype.concat.apply([],children);}}return children;}// 2. When the children contains constructs that always generated nested Arrays,
// e.g. <template>, <slot>, v-for, or when the children is provided by user
// with hand-written render functions / JSX. In such cases a full normalization
// is needed to cater to all possible types of children values.
function normalizeChildren(children){return isPrimitive(children)?[createTextVNode(children)]:Array.isArray(children)?normalizeArrayChildren(children):undefined;}function isTextNode(node){return isDef(node)&&isDef(node.text)&&isFalse(node.isComment);}function normalizeArrayChildren(children,nestedIndex){var res=[];var i,c,lastIndex,last;for(i=0;i<children.length;i++){c=children[i];if(isUndef(c)||typeof c==='boolean'){continue;}lastIndex=res.length-1;last=res[lastIndex];//  nested
if(Array.isArray(c)){if(c.length>0){c=normalizeArrayChildren(c,(nestedIndex||'')+"_"+i);// merge adjacent text nodes
if(isTextNode(c[0])&&isTextNode(last)){res[lastIndex]=createTextVNode(last.text+c[0].text);c.shift();}res.push.apply(res,c);}}else if(isPrimitive(c)){if(isTextNode(last)){// merge adjacent text nodes
// this is necessary for SSR hydration because text nodes are
// essentially merged when rendered to HTML strings
res[lastIndex]=createTextVNode(last.text+c);}else if(c!==''){// convert primitive to vnode
res.push(createTextVNode(c));}}else{if(isTextNode(c)&&isTextNode(last)){// merge adjacent text nodes
res[lastIndex]=createTextVNode(last.text+c.text);}else{// default key for nested array children (likely generated by v-for)
if(isTrue(children._isVList)&&isDef(c.tag)&&isUndef(c.key)&&isDef(nestedIndex)){c.key="__vlist"+nestedIndex+"_"+i+"__";}res.push(c);}}}return res;}/*  */function initProvide(vm){var provide=vm.$options.provide;if(provide){vm._provided=typeof provide==='function'?provide.call(vm):provide;}}function initInjections(vm){var result=resolveInject(vm.$options.inject,vm);if(result){toggleObserving(false);Object.keys(result).forEach(function(key){/* istanbul ignore else */{defineReactive$$1(vm,key,result[key]);}});toggleObserving(true);}}function resolveInject(inject,vm){if(inject){// inject is :any because flow is not smart enough to figure out cached
var result=Object.create(null);var keys=hasSymbol?Reflect.ownKeys(inject):Object.keys(inject);for(var i=0;i<keys.length;i++){var key=keys[i];// #6574 in case the inject object is observed...
if(key==='__ob__'){continue;}var provideKey=inject[key].from;var source=vm;while(source){if(source._provided&&hasOwn(source._provided,provideKey)){result[key]=source._provided[provideKey];break;}source=source.$parent;}if(!source){if('default'in inject[key]){var provideDefault=inject[key]["default"];result[key]=typeof provideDefault==='function'?provideDefault.call(vm):provideDefault;}}}return result;}}/*  */ /**
   * Runtime helper for resolving raw children VNodes into a slot object.
   */function resolveSlots(children,context){if(!children||!children.length){return{};}var slots={};for(var i=0,l=children.length;i<l;i++){var child=children[i];var data=child.data;// remove slot attribute if the node is resolved as a Vue slot node
if(data&&data.attrs&&data.attrs.slot){delete data.attrs.slot;}// named slots should only be respected if the vnode was rendered in the
// same context.
if((child.context===context||child.fnContext===context)&&data&&data.slot!=null){var name=data.slot;var slot=slots[name]||(slots[name]=[]);if(child.tag==='template'){slot.push.apply(slot,child.children||[]);}else{slot.push(child);}}else{(slots["default"]||(slots["default"]=[])).push(child);}}// ignore slots that contains only whitespace
for(var name$1 in slots){if(slots[name$1].every(isWhitespace)){delete slots[name$1];}}return slots;}function isWhitespace(node){return node.isComment&&!node.asyncFactory||node.text===' ';}/*  */function normalizeScopedSlots(slots,normalSlots,prevSlots){var res;var hasNormalSlots=Object.keys(normalSlots).length>0;var isStable=slots?!!slots.$stable:!hasNormalSlots;var key=slots&&slots.$key;if(!slots){res={};}else if(slots._normalized){// fast path 1: child component re-render only, parent did not change
return slots._normalized;}else if(isStable&&prevSlots&&prevSlots!==emptyObject&&key===prevSlots.$key&&!hasNormalSlots&&!prevSlots.$hasNormal){// fast path 2: stable scoped slots w/ no normal slots to proxy,
// only need to normalize once
return prevSlots;}else{res={};for(var key$1 in slots){if(slots[key$1]&&key$1[0]!=='$'){res[key$1]=normalizeScopedSlot(normalSlots,key$1,slots[key$1]);}}}// expose normal slots on scopedSlots
for(var key$2 in normalSlots){if(!(key$2 in res)){res[key$2]=proxyNormalSlot(normalSlots,key$2);}}// avoriaz seems to mock a non-extensible $scopedSlots object
// and when that is passed down this would cause an error
if(slots&&Object.isExtensible(slots)){slots._normalized=res;}def(res,'$stable',isStable);def(res,'$key',key);def(res,'$hasNormal',hasNormalSlots);return res;}function normalizeScopedSlot(normalSlots,key,fn){var normalized=function normalized(){var res=arguments.length?fn.apply(null,arguments):fn({});res=res&&_typeof2(res)==='object'&&!Array.isArray(res)?[res]// single vnode
:normalizeChildren(res);return res&&(res.length===0||res.length===1&&res[0].isComment// #9658
)?undefined:res;};// this is a slot using the new v-slot syntax without scope. although it is
// compiled as a scoped slot, render fn users would expect it to be present
// on this.$slots because the usage is semantically a normal slot.
if(fn.proxy){Object.defineProperty(normalSlots,key,{get:normalized,enumerable:true,configurable:true});}return normalized;}function proxyNormalSlot(slots,key){return function(){return slots[key];};}/*  */ /**
   * Runtime helper for rendering v-for lists.
   */function renderList(val,render){var ret,i,l,keys,key;if(Array.isArray(val)||typeof val==='string'){ret=new Array(val.length);for(i=0,l=val.length;i<l;i++){ret[i]=render(val[i],i);}}else if(typeof val==='number'){ret=new Array(val);for(i=0;i<val;i++){ret[i]=render(i+1,i);}}else if(isObject(val)){if(hasSymbol&&val[Symbol.iterator]){ret=[];var iterator=val[Symbol.iterator]();var result=iterator.next();while(!result.done){ret.push(render(result.value,ret.length));result=iterator.next();}}else{keys=Object.keys(val);ret=new Array(keys.length);for(i=0,l=keys.length;i<l;i++){key=keys[i];ret[i]=render(val[key],key,i);}}}if(!isDef(ret)){ret=[];}ret._isVList=true;return ret;}/*  */ /**
   * Runtime helper for rendering <slot>
   */function renderSlot(name,fallback,props,bindObject){var scopedSlotFn=this.$scopedSlots[name];var nodes;if(scopedSlotFn){// scoped slot
props=props||{};if(bindObject){props=extend(extend({},bindObject),props);}nodes=scopedSlotFn(props)||fallback;}else{nodes=this.$slots[name]||fallback;}var target=props&&props.slot;if(target){return this.$createElement('template',{slot:target},nodes);}else{return nodes;}}/*  */ /**
   * Runtime helper for resolving filters
   */function resolveFilter(id){return resolveAsset(this.$options,'filters',id)||identity;}/*  */function isKeyNotMatch(expect,actual){if(Array.isArray(expect)){return expect.indexOf(actual)===-1;}else{return expect!==actual;}}/**
   * Runtime helper for checking keyCodes from config.
   * exposed as Vue.prototype._k
   * passing in eventKeyName as last argument separately for backwards compat
   */function checkKeyCodes(eventKeyCode,key,builtInKeyCode,eventKeyName,builtInKeyName){var mappedKeyCode=config.keyCodes[key]||builtInKeyCode;if(builtInKeyName&&eventKeyName&&!config.keyCodes[key]){return isKeyNotMatch(builtInKeyName,eventKeyName);}else if(mappedKeyCode){return isKeyNotMatch(mappedKeyCode,eventKeyCode);}else if(eventKeyName){return hyphenate(eventKeyName)!==key;}}/*  */ /**
   * Runtime helper for merging v-bind="object" into a VNode's data.
   */function bindObjectProps(data,tag,value,asProp,isSync){if(value){if(!isObject(value));else{if(Array.isArray(value)){value=toObject(value);}var hash;var loop=function loop(key){if(key==='class'||key==='style'||isReservedAttribute(key)){hash=data;}else{var type=data.attrs&&data.attrs.type;hash=asProp||config.mustUseProp(tag,type,key)?data.domProps||(data.domProps={}):data.attrs||(data.attrs={});}var camelizedKey=camelize(key);var hyphenatedKey=hyphenate(key);if(!(camelizedKey in hash)&&!(hyphenatedKey in hash)){hash[key]=value[key];if(isSync){var on=data.on||(data.on={});on["update:"+key]=function($event){value[key]=$event;};}}};for(var key in value){loop(key);}}}return data;}/*  */ /**
   * Runtime helper for rendering static trees.
   */function renderStatic(index,isInFor){var cached=this._staticTrees||(this._staticTrees=[]);var tree=cached[index];// if has already-rendered static tree and not inside v-for,
// we can reuse the same tree.
if(tree&&!isInFor){return tree;}// otherwise, render a fresh tree.
tree=cached[index]=this.$options.staticRenderFns[index].call(this._renderProxy,null,this// for render fns generated for functional component templates
);markStatic(tree,"__static__"+index,false);return tree;}/**
   * Runtime helper for v-once.
   * Effectively it means marking the node as static with a unique key.
   */function markOnce(tree,index,key){markStatic(tree,"__once__"+index+(key?"_"+key:""),true);return tree;}function markStatic(tree,key,isOnce){if(Array.isArray(tree)){for(var i=0;i<tree.length;i++){if(tree[i]&&typeof tree[i]!=='string'){markStaticNode(tree[i],key+"_"+i,isOnce);}}}else{markStaticNode(tree,key,isOnce);}}function markStaticNode(node,key,isOnce){node.isStatic=true;node.key=key;node.isOnce=isOnce;}/*  */function bindObjectListeners(data,value){if(value){if(!isPlainObject(value));else{var on=data.on=data.on?extend({},data.on):{};for(var key in value){var existing=on[key];var ours=value[key];on[key]=existing?[].concat(existing,ours):ours;}}}return data;}/*  */function resolveScopedSlots(fns,// see flow/vnode
res,// the following are added in 2.6
hasDynamicKeys,contentHashKey){res=res||{$stable:!hasDynamicKeys};for(var i=0;i<fns.length;i++){var slot=fns[i];if(Array.isArray(slot)){resolveScopedSlots(slot,res,hasDynamicKeys);}else if(slot){// marker for reverse proxying v-slot without scope on this.$slots
if(slot.proxy){slot.fn.proxy=true;}res[slot.key]=slot.fn;}}if(contentHashKey){res.$key=contentHashKey;}return res;}/*  */function bindDynamicKeys(baseObj,values){for(var i=0;i<values.length;i+=2){var key=values[i];if(typeof key==='string'&&key){baseObj[values[i]]=values[i+1];}}return baseObj;}// helper to dynamically append modifier runtime markers to event names.
// ensure only append when value is already string, otherwise it will be cast
// to string and cause the type check to miss.
function prependModifier(value,symbol){return typeof value==='string'?symbol+value:value;}/*  */function installRenderHelpers(target){target._o=markOnce;target._n=toNumber;target._s=toString;target._l=renderList;target._t=renderSlot;target._q=looseEqual;target._i=looseIndexOf;target._m=renderStatic;target._f=resolveFilter;target._k=checkKeyCodes;target._b=bindObjectProps;target._v=createTextVNode;target._e=createEmptyVNode;target._u=resolveScopedSlots;target._g=bindObjectListeners;target._d=bindDynamicKeys;target._p=prependModifier;}/*  */function FunctionalRenderContext(data,props,children,parent,Ctor){var this$1=this;var options=Ctor.options;// ensure the createElement function in functional components
// gets a unique context - this is necessary for correct named slot check
var contextVm;if(hasOwn(parent,'_uid')){contextVm=Object.create(parent);// $flow-disable-line
contextVm._original=parent;}else{// the context vm passed in is a functional context as well.
// in this case we want to make sure we are able to get a hold to the
// real context instance.
contextVm=parent;// $flow-disable-line
parent=parent._original;}var isCompiled=isTrue(options._compiled);var needNormalization=!isCompiled;this.data=data;this.props=props;this.children=children;this.parent=parent;this.listeners=data.on||emptyObject;this.injections=resolveInject(options.inject,parent);this.slots=function(){if(!this$1.$slots){normalizeScopedSlots(data.scopedSlots,this$1.$slots=resolveSlots(children,parent));}return this$1.$slots;};Object.defineProperty(this,'scopedSlots',{enumerable:true,get:function get(){return normalizeScopedSlots(data.scopedSlots,this.slots());}});// support for compiled functional template
if(isCompiled){// exposing $options for renderStatic()
this.$options=options;// pre-resolve slots for renderSlot()
this.$slots=this.slots();this.$scopedSlots=normalizeScopedSlots(data.scopedSlots,this.$slots);}if(options._scopeId){this._c=function(a,b,c,d){var vnode=createElement(contextVm,a,b,c,d,needNormalization);if(vnode&&!Array.isArray(vnode)){vnode.fnScopeId=options._scopeId;vnode.fnContext=parent;}return vnode;};}else{this._c=function(a,b,c,d){return createElement(contextVm,a,b,c,d,needNormalization);};}}installRenderHelpers(FunctionalRenderContext.prototype);function createFunctionalComponent(Ctor,propsData,data,contextVm,children){var options=Ctor.options;var props={};var propOptions=options.props;if(isDef(propOptions)){for(var key in propOptions){props[key]=validateProp(key,propOptions,propsData||emptyObject);}}else{if(isDef(data.attrs)){mergeProps(props,data.attrs);}if(isDef(data.props)){mergeProps(props,data.props);}}var renderContext=new FunctionalRenderContext(data,props,children,contextVm,Ctor);var vnode=options.render.call(null,renderContext._c,renderContext);if(vnode instanceof VNode){return cloneAndMarkFunctionalResult(vnode,data,renderContext.parent,options);}else if(Array.isArray(vnode)){var vnodes=normalizeChildren(vnode)||[];var res=new Array(vnodes.length);for(var i=0;i<vnodes.length;i++){res[i]=cloneAndMarkFunctionalResult(vnodes[i],data,renderContext.parent,options);}return res;}}function cloneAndMarkFunctionalResult(vnode,data,contextVm,options,renderContext){// #7817 clone node before setting fnContext, otherwise if the node is reused
// (e.g. it was from a cached normal slot) the fnContext causes named slots
// that should not be matched to match.
var clone=cloneVNode(vnode);clone.fnContext=contextVm;clone.fnOptions=options;if(data.slot){(clone.data||(clone.data={})).slot=data.slot;}return clone;}function mergeProps(to,from){for(var key in from){to[camelize(key)]=from[key];}}/*  */ /*  */ /*  */ /*  */ // inline hooks to be invoked on component VNodes during patch
var componentVNodeHooks={init:function init(vnode,hydrating){if(vnode.componentInstance&&!vnode.componentInstance._isDestroyed&&vnode.data.keepAlive){// kept-alive components, treat as a patch
var mountedNode=vnode;// work around flow
componentVNodeHooks.prepatch(mountedNode,mountedNode);}else{var child=vnode.componentInstance=createComponentInstanceForVnode(vnode,activeInstance);child.$mount(hydrating?vnode.elm:undefined,hydrating);}},prepatch:function prepatch(oldVnode,vnode){var options=vnode.componentOptions;var child=vnode.componentInstance=oldVnode.componentInstance;updateChildComponent(child,options.propsData,// updated props
options.listeners,// updated listeners
vnode,// new parent vnode
options.children// new children
);},insert:function insert(vnode){var context=vnode.context;var componentInstance=vnode.componentInstance;if(!componentInstance._isMounted){componentInstance._isMounted=true;callHook(componentInstance,'mounted');}if(vnode.data.keepAlive){if(context._isMounted){// vue-router#1212
// During updates, a kept-alive component's child components may
// change, so directly walking the tree here may call activated hooks
// on incorrect children. Instead we push them into a queue which will
// be processed after the whole patch process ended.
queueActivatedComponent(componentInstance);}else{activateChildComponent(componentInstance,true/* direct */);}}},destroy:function destroy(vnode){var componentInstance=vnode.componentInstance;if(!componentInstance._isDestroyed){if(!vnode.data.keepAlive){componentInstance.$destroy();}else{deactivateChildComponent(componentInstance,true/* direct */);}}}};var hooksToMerge=Object.keys(componentVNodeHooks);function createComponent(Ctor,data,context,children,tag){if(isUndef(Ctor)){return;}var baseCtor=context.$options._base;// plain options object: turn it into a constructor
if(isObject(Ctor)){Ctor=baseCtor.extend(Ctor);}// if at this stage it's not a constructor or an async component factory,
// reject.
if(typeof Ctor!=='function'){return;}// async component
var asyncFactory;if(isUndef(Ctor.cid)){asyncFactory=Ctor;Ctor=resolveAsyncComponent(asyncFactory,baseCtor);if(Ctor===undefined){// return a placeholder node for async component, which is rendered
// as a comment node but preserves all the raw information for the node.
// the information will be used for async server-rendering and hydration.
return createAsyncPlaceholder(asyncFactory,data,context,children,tag);}}data=data||{};// resolve constructor options in case global mixins are applied after
// component constructor creation
resolveConstructorOptions(Ctor);// transform component v-model data into props & events
if(isDef(data.model)){transformModel(Ctor.options,data);}// extract props
var propsData=extractPropsFromVNodeData(data,Ctor);// functional component
if(isTrue(Ctor.options.functional)){return createFunctionalComponent(Ctor,propsData,data,context,children);}// extract listeners, since these needs to be treated as
// child component listeners instead of DOM listeners
var listeners=data.on;// replace with listeners with .native modifier
// so it gets processed during parent component patch.
data.on=data.nativeOn;if(isTrue(Ctor.options["abstract"])){// abstract components do not keep anything
// other than props & listeners & slot
// work around flow
var slot=data.slot;data={};if(slot){data.slot=slot;}}// install component management hooks onto the placeholder node
installComponentHooks(data);// return a placeholder vnode
var name=Ctor.options.name||tag;var vnode=new VNode("vue-component-"+Ctor.cid+(name?"-"+name:''),data,undefined,undefined,undefined,context,{Ctor:Ctor,propsData:propsData,listeners:listeners,tag:tag,children:children},asyncFactory);return vnode;}function createComponentInstanceForVnode(vnode,// we know it's MountedComponentVNode but flow doesn't
parent// activeInstance in lifecycle state
){var options={_isComponent:true,_parentVnode:vnode,parent:parent};// check inline-template render functions
var inlineTemplate=vnode.data.inlineTemplate;if(isDef(inlineTemplate)){options.render=inlineTemplate.render;options.staticRenderFns=inlineTemplate.staticRenderFns;}return new vnode.componentOptions.Ctor(options);}function installComponentHooks(data){var hooks=data.hook||(data.hook={});for(var i=0;i<hooksToMerge.length;i++){var key=hooksToMerge[i];var existing=hooks[key];var toMerge=componentVNodeHooks[key];if(existing!==toMerge&&!(existing&&existing._merged)){hooks[key]=existing?mergeHook$1(toMerge,existing):toMerge;}}}function mergeHook$1(f1,f2){var merged=function merged(a,b){// flow complains about extra args which is why we use any
f1(a,b);f2(a,b);};merged._merged=true;return merged;}// transform component v-model info (value and callback) into
// prop and event handler respectively.
function transformModel(options,data){var prop=options.model&&options.model.prop||'value';var event=options.model&&options.model.event||'input';(data.attrs||(data.attrs={}))[prop]=data.model.value;var on=data.on||(data.on={});var existing=on[event];var callback=data.model.callback;if(isDef(existing)){if(Array.isArray(existing)?existing.indexOf(callback)===-1:existing!==callback){on[event]=[callback].concat(existing);}}else{on[event]=callback;}}/*  */var SIMPLE_NORMALIZE=1;var ALWAYS_NORMALIZE=2;// wrapper function for providing a more flexible interface
// without getting yelled at by flow
function createElement(context,tag,data,children,normalizationType,alwaysNormalize){if(Array.isArray(data)||isPrimitive(data)){normalizationType=children;children=data;data=undefined;}if(isTrue(alwaysNormalize)){normalizationType=ALWAYS_NORMALIZE;}return _createElement(context,tag,data,children,normalizationType);}function _createElement(context,tag,data,children,normalizationType){if(isDef(data)&&isDef(data.__ob__)){return createEmptyVNode();}// object syntax in v-bind
if(isDef(data)&&isDef(data.is)){tag=data.is;}if(!tag){// in case of component :is set to falsy value
return createEmptyVNode();}// support single function children as default scoped slot
if(Array.isArray(children)&&typeof children[0]==='function'){data=data||{};data.scopedSlots={"default":children[0]};children.length=0;}if(normalizationType===ALWAYS_NORMALIZE){children=normalizeChildren(children);}else if(normalizationType===SIMPLE_NORMALIZE){children=simpleNormalizeChildren(children);}var vnode,ns;if(typeof tag==='string'){var Ctor;ns=context.$vnode&&context.$vnode.ns||config.getTagNamespace(tag);if(config.isReservedTag(tag)){// platform built-in elements
vnode=new VNode(config.parsePlatformTagName(tag),data,children,undefined,undefined,context);}else if((!data||!data.pre)&&isDef(Ctor=resolveAsset(context.$options,'components',tag))){// component
vnode=createComponent(Ctor,data,context,children,tag);}else{// unknown or unlisted namespaced elements
// check at runtime because it may get assigned a namespace when its
// parent normalizes children
vnode=new VNode(tag,data,children,undefined,undefined,context);}}else{// direct component options / constructor
vnode=createComponent(tag,data,context,children);}if(Array.isArray(vnode)){return vnode;}else if(isDef(vnode)){if(isDef(ns)){applyNS(vnode,ns);}if(isDef(data)){registerDeepBindings(data);}return vnode;}else{return createEmptyVNode();}}function applyNS(vnode,ns,force){vnode.ns=ns;if(vnode.tag==='foreignObject'){// use default namespace inside foreignObject
ns=undefined;force=true;}if(isDef(vnode.children)){for(var i=0,l=vnode.children.length;i<l;i++){var child=vnode.children[i];if(isDef(child.tag)&&(isUndef(child.ns)||isTrue(force)&&child.tag!=='svg')){applyNS(child,ns,force);}}}}// ref #5318
// necessary to ensure parent re-render when deep bindings like :style and
// :class are used on slot nodes
function registerDeepBindings(data){if(isObject(data.style)){traverse(data.style);}if(isObject(data["class"])){traverse(data["class"]);}}/*  */function initRender(vm){vm._vnode=null;// the root of the child tree
vm._staticTrees=null;// v-once cached trees
var options=vm.$options;var parentVnode=vm.$vnode=options._parentVnode;// the placeholder node in parent tree
var renderContext=parentVnode&&parentVnode.context;vm.$slots=resolveSlots(options._renderChildren,renderContext);vm.$scopedSlots=emptyObject;// bind the createElement fn to this instance
// so that we get proper render context inside it.
// args order: tag, data, children, normalizationType, alwaysNormalize
// internal version is used by render functions compiled from templates
vm._c=function(a,b,c,d){return createElement(vm,a,b,c,d,false);};// normalization is always applied for the public version, used in
// user-written render functions.
vm.$createElement=function(a,b,c,d){return createElement(vm,a,b,c,d,true);};// $attrs & $listeners are exposed for easier HOC creation.
// they need to be reactive so that HOCs using them are always updated
var parentData=parentVnode&&parentVnode.data;/* istanbul ignore else */{defineReactive$$1(vm,'$attrs',parentData&&parentData.attrs||emptyObject,null,true);defineReactive$$1(vm,'$listeners',options._parentListeners||emptyObject,null,true);}}var currentRenderingInstance=null;function renderMixin(Vue){// install runtime convenience helpers
installRenderHelpers(Vue.prototype);Vue.prototype.$nextTick=function(fn){return nextTick(fn,this);};Vue.prototype._render=function(){var vm=this;var ref=vm.$options;var render=ref.render;var _parentVnode=ref._parentVnode;if(_parentVnode){vm.$scopedSlots=normalizeScopedSlots(_parentVnode.data.scopedSlots,vm.$slots,vm.$scopedSlots);}// set parent vnode. this allows render functions to have access
// to the data on the placeholder node.
vm.$vnode=_parentVnode;// render self
var vnode;try{// There's no need to maintain a stack becaues all render fns are called
// separately from one another. Nested component's render fns are called
// when parent component is patched.
currentRenderingInstance=vm;vnode=render.call(vm._renderProxy,vm.$createElement);}catch(e){handleError(e,vm,"render");// return error render result,
// or previous vnode to prevent render error causing blank component
/* istanbul ignore else */{vnode=vm._vnode;}}finally{currentRenderingInstance=null;}// if the returned array contains only a single node, allow it
if(Array.isArray(vnode)&&vnode.length===1){vnode=vnode[0];}// return empty vnode in case the render function errored out
if(!(vnode instanceof VNode)){vnode=createEmptyVNode();}// set parent
vnode.parent=_parentVnode;return vnode;};}/*  */function ensureCtor(comp,base){if(comp.__esModule||hasSymbol&&comp[Symbol.toStringTag]==='Module'){comp=comp["default"];}return isObject(comp)?base.extend(comp):comp;}function createAsyncPlaceholder(factory,data,context,children,tag){var node=createEmptyVNode();node.asyncFactory=factory;node.asyncMeta={data:data,context:context,children:children,tag:tag};return node;}function resolveAsyncComponent(factory,baseCtor){if(isTrue(factory.error)&&isDef(factory.errorComp)){return factory.errorComp;}if(isDef(factory.resolved)){return factory.resolved;}var owner=currentRenderingInstance;if(owner&&isDef(factory.owners)&&factory.owners.indexOf(owner)===-1){// already pending
factory.owners.push(owner);}if(isTrue(factory.loading)&&isDef(factory.loadingComp)){return factory.loadingComp;}if(owner&&!isDef(factory.owners)){var owners=factory.owners=[owner];var sync=true;var timerLoading=null;var timerTimeout=null;owner.$on('hook:destroyed',function(){return remove(owners,owner);});var forceRender=function forceRender(renderCompleted){for(var i=0,l=owners.length;i<l;i++){owners[i].$forceUpdate();}if(renderCompleted){owners.length=0;if(timerLoading!==null){clearTimeout(timerLoading);timerLoading=null;}if(timerTimeout!==null){clearTimeout(timerTimeout);timerTimeout=null;}}};var resolve=once(function(res){// cache resolved
factory.resolved=ensureCtor(res,baseCtor);// invoke callbacks only if this is not a synchronous resolve
// (async resolves are shimmed as synchronous during SSR)
if(!sync){forceRender(true);}else{owners.length=0;}});var reject=once(function(reason){if(isDef(factory.errorComp)){factory.error=true;forceRender(true);}});var res=factory(resolve,reject);if(isObject(res)){if(isPromise(res)){// () => Promise
if(isUndef(factory.resolved)){res.then(resolve,reject);}}else if(isPromise(res.component)){res.component.then(resolve,reject);if(isDef(res.error)){factory.errorComp=ensureCtor(res.error,baseCtor);}if(isDef(res.loading)){factory.loadingComp=ensureCtor(res.loading,baseCtor);if(res.delay===0){factory.loading=true;}else{timerLoading=setTimeout(function(){timerLoading=null;if(isUndef(factory.resolved)&&isUndef(factory.error)){factory.loading=true;forceRender(false);}},res.delay||200);}}if(isDef(res.timeout)){timerTimeout=setTimeout(function(){timerTimeout=null;if(isUndef(factory.resolved)){reject(null);}},res.timeout);}}}sync=false;// return in case resolved synchronously
return factory.loading?factory.loadingComp:factory.resolved;}}/*  */function isAsyncPlaceholder(node){return node.isComment&&node.asyncFactory;}/*  */function getFirstComponentChild(children){if(Array.isArray(children)){for(var i=0;i<children.length;i++){var c=children[i];if(isDef(c)&&(isDef(c.componentOptions)||isAsyncPlaceholder(c))){return c;}}}}/*  */ /*  */function initEvents(vm){vm._events=Object.create(null);vm._hasHookEvent=false;// init parent attached events
var listeners=vm.$options._parentListeners;if(listeners){updateComponentListeners(vm,listeners);}}var target;function add(event,fn){target.$on(event,fn);}function remove$1(event,fn){target.$off(event,fn);}function createOnceHandler(event,fn){var _target=target;return function onceHandler(){var res=fn.apply(null,arguments);if(res!==null){_target.$off(event,onceHandler);}};}function updateComponentListeners(vm,listeners,oldListeners){target=vm;updateListeners(listeners,oldListeners||{},add,remove$1,createOnceHandler,vm);target=undefined;}function eventsMixin(Vue){var hookRE=/^hook:/;Vue.prototype.$on=function(event,fn){var vm=this;if(Array.isArray(event)){for(var i=0,l=event.length;i<l;i++){vm.$on(event[i],fn);}}else{(vm._events[event]||(vm._events[event]=[])).push(fn);// optimize hook:event cost by using a boolean flag marked at registration
// instead of a hash lookup
if(hookRE.test(event)){vm._hasHookEvent=true;}}return vm;};Vue.prototype.$once=function(event,fn){var vm=this;function on(){vm.$off(event,on);fn.apply(vm,arguments);}on.fn=fn;vm.$on(event,on);return vm;};Vue.prototype.$off=function(event,fn){var vm=this;// all
if(!arguments.length){vm._events=Object.create(null);return vm;}// array of events
if(Array.isArray(event)){for(var i$1=0,l=event.length;i$1<l;i$1++){vm.$off(event[i$1],fn);}return vm;}// specific event
var cbs=vm._events[event];if(!cbs){return vm;}if(!fn){vm._events[event]=null;return vm;}// specific handler
var cb;var i=cbs.length;while(i--){cb=cbs[i];if(cb===fn||cb.fn===fn){cbs.splice(i,1);break;}}return vm;};Vue.prototype.$emit=function(event){var vm=this;var cbs=vm._events[event];if(cbs){cbs=cbs.length>1?toArray(cbs):cbs;var args=toArray(arguments,1);var info="event handler for \""+event+"\"";for(var i=0,l=cbs.length;i<l;i++){invokeWithErrorHandling(cbs[i],vm,args,vm,info);}}return vm;};}/*  */var activeInstance=null;function setActiveInstance(vm){var prevActiveInstance=activeInstance;activeInstance=vm;return function(){activeInstance=prevActiveInstance;};}function initLifecycle(vm){var options=vm.$options;// locate first non-abstract parent
var parent=options.parent;if(parent&&!options["abstract"]){while(parent.$options["abstract"]&&parent.$parent){parent=parent.$parent;}parent.$children.push(vm);}vm.$parent=parent;vm.$root=parent?parent.$root:vm;vm.$children=[];vm.$refs={};vm._watcher=null;vm._inactive=null;vm._directInactive=false;vm._isMounted=false;vm._isDestroyed=false;vm._isBeingDestroyed=false;}function lifecycleMixin(Vue){Vue.prototype._update=function(vnode,hydrating){var vm=this;var prevEl=vm.$el;var prevVnode=vm._vnode;var restoreActiveInstance=setActiveInstance(vm);vm._vnode=vnode;// Vue.prototype.__patch__ is injected in entry points
// based on the rendering backend used.
if(!prevVnode){// initial render
vm.$el=vm.__patch__(vm.$el,vnode,hydrating,false/* removeOnly */);}else{// updates
vm.$el=vm.__patch__(prevVnode,vnode);}restoreActiveInstance();// update __vue__ reference
if(prevEl){prevEl.__vue__=null;}if(vm.$el){vm.$el.__vue__=vm;}// if parent is an HOC, update its $el as well
if(vm.$vnode&&vm.$parent&&vm.$vnode===vm.$parent._vnode){vm.$parent.$el=vm.$el;}// updated hook is called by the scheduler to ensure that children are
// updated in a parent's updated hook.
};Vue.prototype.$forceUpdate=function(){var vm=this;if(vm._watcher){vm._watcher.update();}};Vue.prototype.$destroy=function(){var vm=this;if(vm._isBeingDestroyed){return;}callHook(vm,'beforeDestroy');vm._isBeingDestroyed=true;// remove self from parent
var parent=vm.$parent;if(parent&&!parent._isBeingDestroyed&&!vm.$options["abstract"]){remove(parent.$children,vm);}// teardown watchers
if(vm._watcher){vm._watcher.teardown();}var i=vm._watchers.length;while(i--){vm._watchers[i].teardown();}// remove reference from data ob
// frozen object may not have observer.
if(vm._data.__ob__){vm._data.__ob__.vmCount--;}// call the last hook...
vm._isDestroyed=true;// invoke destroy hooks on current rendered tree
vm.__patch__(vm._vnode,null);// fire destroyed hook
callHook(vm,'destroyed');// turn off all instance listeners.
vm.$off();// remove __vue__ reference
if(vm.$el){vm.$el.__vue__=null;}// release circular reference (#6759)
if(vm.$vnode){vm.$vnode.parent=null;}};}function mountComponent(vm,el,hydrating){vm.$el=el;if(!vm.$options.render){vm.$options.render=createEmptyVNode;}callHook(vm,'beforeMount');var updateComponent;/* istanbul ignore if */{updateComponent=function updateComponent(){vm._update(vm._render(),hydrating);};}// we set this to vm._watcher inside the watcher's constructor
// since the watcher's initial patch may call $forceUpdate (e.g. inside child
// component's mounted hook), which relies on vm._watcher being already defined
new Watcher(vm,updateComponent,noop,{before:function before(){if(vm._isMounted&&!vm._isDestroyed){callHook(vm,'beforeUpdate');}}},true/* isRenderWatcher */);hydrating=false;// manually mounted instance, call mounted on self
// mounted is called for render-created child components in its inserted hook
if(vm.$vnode==null){vm._isMounted=true;callHook(vm,'mounted');}return vm;}function updateChildComponent(vm,propsData,listeners,parentVnode,renderChildren){// determine whether component has slot children
// we need to do this before overwriting $options._renderChildren.
// check if there are dynamic scopedSlots (hand-written or compiled but with
// dynamic slot names). Static scoped slots compiled from template has the
// "$stable" marker.
var newScopedSlots=parentVnode.data.scopedSlots;var oldScopedSlots=vm.$scopedSlots;var hasDynamicScopedSlot=!!(newScopedSlots&&!newScopedSlots.$stable||oldScopedSlots!==emptyObject&&!oldScopedSlots.$stable||newScopedSlots&&vm.$scopedSlots.$key!==newScopedSlots.$key);// Any static slot children from the parent may have changed during parent's
// update. Dynamic scoped slots may also have changed. In such cases, a forced
// update is necessary to ensure correctness.
var needsForceUpdate=!!(renderChildren||// has new static slots
vm.$options._renderChildren||// has old static slots
hasDynamicScopedSlot);vm.$options._parentVnode=parentVnode;vm.$vnode=parentVnode;// update vm's placeholder node without re-render
if(vm._vnode){// update child tree's parent
vm._vnode.parent=parentVnode;}vm.$options._renderChildren=renderChildren;// update $attrs and $listeners hash
// these are also reactive so they may trigger child update if the child
// used them during render
vm.$attrs=parentVnode.data.attrs||emptyObject;vm.$listeners=listeners||emptyObject;// update props
if(propsData&&vm.$options.props){toggleObserving(false);var props=vm._props;var propKeys=vm.$options._propKeys||[];for(var i=0;i<propKeys.length;i++){var key=propKeys[i];var propOptions=vm.$options.props;// wtf flow?
props[key]=validateProp(key,propOptions,propsData,vm);}toggleObserving(true);// keep a copy of raw propsData
vm.$options.propsData=propsData;}// update listeners
listeners=listeners||emptyObject;var oldListeners=vm.$options._parentListeners;vm.$options._parentListeners=listeners;updateComponentListeners(vm,listeners,oldListeners);// resolve slots + force update if has children
if(needsForceUpdate){vm.$slots=resolveSlots(renderChildren,parentVnode.context);vm.$forceUpdate();}}function isInInactiveTree(vm){while(vm&&(vm=vm.$parent)){if(vm._inactive){return true;}}return false;}function activateChildComponent(vm,direct){if(direct){vm._directInactive=false;if(isInInactiveTree(vm)){return;}}else if(vm._directInactive){return;}if(vm._inactive||vm._inactive===null){vm._inactive=false;for(var i=0;i<vm.$children.length;i++){activateChildComponent(vm.$children[i]);}callHook(vm,'activated');}}function deactivateChildComponent(vm,direct){if(direct){vm._directInactive=true;if(isInInactiveTree(vm)){return;}}if(!vm._inactive){vm._inactive=true;for(var i=0;i<vm.$children.length;i++){deactivateChildComponent(vm.$children[i]);}callHook(vm,'deactivated');}}function callHook(vm,hook){// #7573 disable dep collection when invoking lifecycle hooks
pushTarget();var handlers=vm.$options[hook];var info=hook+" hook";if(handlers){for(var i=0,j=handlers.length;i<j;i++){invokeWithErrorHandling(handlers[i],vm,null,vm,info);}}if(vm._hasHookEvent){vm.$emit('hook:'+hook);}popTarget();}var queue=[];var activatedChildren=[];var has={};var waiting=false;var flushing=false;var index=0;/**
   * Reset the scheduler's state.
   */function resetSchedulerState(){index=queue.length=activatedChildren.length=0;has={};waiting=flushing=false;}// Async edge case #6566 requires saving the timestamp when event listeners are
// attached. However, calling performance.now() has a perf overhead especially
// if the page has thousands of event listeners. Instead, we take a timestamp
// every time the scheduler flushes and use that for all event listeners
// attached during that flush.
var currentFlushTimestamp=0;// Async edge case fix requires storing an event listener's attach timestamp.
var getNow=Date.now;// Determine what event timestamp the browser is using. Annoyingly, the
// timestamp can either be hi-res (relative to page load) or low-res
// (relative to UNIX epoch), so in order to compare time we have to use the
// same timestamp type when saving the flush timestamp.
// All IE versions use low-res event timestamps, and have problematic clock
// implementations (#9632)
if(inBrowser&&!isIE){var performance=window.performance;if(performance&&typeof performance.now==='function'&&getNow()>document.createEvent('Event').timeStamp){// if the event timestamp, although evaluated AFTER the Date.now(), is
// smaller than it, it means the event is using a hi-res timestamp,
// and we need to use the hi-res version for event listener timestamps as
// well.
getNow=function getNow(){return performance.now();};}}/**
   * Flush both queues and run the watchers.
   */function flushSchedulerQueue(){currentFlushTimestamp=getNow();flushing=true;var watcher,id;// Sort queue before flush.
// This ensures that:
// 1. Components are updated from parent to child. (because parent is always
//    created before the child)
// 2. A component's user watchers are run before its render watcher (because
//    user watchers are created before the render watcher)
// 3. If a component is destroyed during a parent component's watcher run,
//    its watchers can be skipped.
queue.sort(function(a,b){return a.id-b.id;});// do not cache length because more watchers might be pushed
// as we run existing watchers
for(index=0;index<queue.length;index++){watcher=queue[index];if(watcher.before){watcher.before();}id=watcher.id;has[id]=null;watcher.run();}// keep copies of post queues before resetting state
var activatedQueue=activatedChildren.slice();var updatedQueue=queue.slice();resetSchedulerState();// call component updated and activated hooks
callActivatedHooks(activatedQueue);callUpdatedHooks(updatedQueue);// devtool hook
/* istanbul ignore if */if(devtools&&config.devtools){devtools.emit('flush');}}function callUpdatedHooks(queue){var i=queue.length;while(i--){var watcher=queue[i];var vm=watcher.vm;if(vm._watcher===watcher&&vm._isMounted&&!vm._isDestroyed){callHook(vm,'updated');}}}/**
   * Queue a kept-alive component that was activated during patch.
   * The queue will be processed after the entire tree has been patched.
   */function queueActivatedComponent(vm){// setting _inactive to false here so that a render function can
// rely on checking whether it's in an inactive tree (e.g. router-view)
vm._inactive=false;activatedChildren.push(vm);}function callActivatedHooks(queue){for(var i=0;i<queue.length;i++){queue[i]._inactive=true;activateChildComponent(queue[i],true/* true */);}}/**
   * Push a watcher into the watcher queue.
   * Jobs with duplicate IDs will be skipped unless it's
   * pushed when the queue is being flushed.
   */function queueWatcher(watcher){var id=watcher.id;if(has[id]==null){has[id]=true;if(!flushing){queue.push(watcher);}else{// if already flushing, splice the watcher based on its id
// if already past its id, it will be run next immediately.
var i=queue.length-1;while(i>index&&queue[i].id>watcher.id){i--;}queue.splice(i+1,0,watcher);}// queue the flush
if(!waiting){waiting=true;nextTick(flushSchedulerQueue);}}}/*  */var uid$2=0;/**
   * A watcher parses an expression, collects dependencies,
   * and fires callback when the expression value changes.
   * This is used for both the $watch() api and directives.
   */var Watcher=function Watcher(vm,expOrFn,cb,options,isRenderWatcher){this.vm=vm;if(isRenderWatcher){vm._watcher=this;}vm._watchers.push(this);// options
if(options){this.deep=!!options.deep;this.user=!!options.user;this.lazy=!!options.lazy;this.sync=!!options.sync;this.before=options.before;}else{this.deep=this.user=this.lazy=this.sync=false;}this.cb=cb;this.id=++uid$2;// uid for batching
this.active=true;this.dirty=this.lazy;// for lazy watchers
this.deps=[];this.newDeps=[];this.depIds=new _Set();this.newDepIds=new _Set();this.expression='';// parse expression for getter
if(typeof expOrFn==='function'){this.getter=expOrFn;}else{this.getter=parsePath(expOrFn);if(!this.getter){this.getter=noop;}}this.value=this.lazy?undefined:this.get();};/**
   * Evaluate the getter, and re-collect dependencies.
   */Watcher.prototype.get=function get(){pushTarget(this);var value;var vm=this.vm;try{value=this.getter.call(vm,vm);}catch(e){if(this.user){handleError(e,vm,"getter for watcher \""+this.expression+"\"");}else{throw e;}}finally{// "touch" every property so they are all tracked as
// dependencies for deep watching
if(this.deep){traverse(value);}popTarget();this.cleanupDeps();}return value;};/**
   * Add a dependency to this directive.
   */Watcher.prototype.addDep=function addDep(dep){var id=dep.id;if(!this.newDepIds.has(id)){this.newDepIds.add(id);this.newDeps.push(dep);if(!this.depIds.has(id)){dep.addSub(this);}}};/**
   * Clean up for dependency collection.
   */Watcher.prototype.cleanupDeps=function cleanupDeps(){var i=this.deps.length;while(i--){var dep=this.deps[i];if(!this.newDepIds.has(dep.id)){dep.removeSub(this);}}var tmp=this.depIds;this.depIds=this.newDepIds;this.newDepIds=tmp;this.newDepIds.clear();tmp=this.deps;this.deps=this.newDeps;this.newDeps=tmp;this.newDeps.length=0;};/**
   * Subscriber interface.
   * Will be called when a dependency changes.
   */Watcher.prototype.update=function update(){/* istanbul ignore else */if(this.lazy){this.dirty=true;}else if(this.sync){this.run();}else{queueWatcher(this);}};/**
   * Scheduler job interface.
   * Will be called by the scheduler.
   */Watcher.prototype.run=function run(){if(this.active){var value=this.get();if(value!==this.value||// Deep watchers and watchers on Object/Arrays should fire even
// when the value is the same, because the value may
// have mutated.
isObject(value)||this.deep){// set new value
var oldValue=this.value;this.value=value;if(this.user){try{this.cb.call(this.vm,value,oldValue);}catch(e){handleError(e,this.vm,"callback for watcher \""+this.expression+"\"");}}else{this.cb.call(this.vm,value,oldValue);}}}};/**
   * Evaluate the value of the watcher.
   * This only gets called for lazy watchers.
   */Watcher.prototype.evaluate=function evaluate(){this.value=this.get();this.dirty=false;};/**
   * Depend on all deps collected by this watcher.
   */Watcher.prototype.depend=function depend(){var i=this.deps.length;while(i--){this.deps[i].depend();}};/**
   * Remove self from all dependencies' subscriber list.
   */Watcher.prototype.teardown=function teardown(){if(this.active){// remove self from vm's watcher list
// this is a somewhat expensive operation so we skip it
// if the vm is being destroyed.
if(!this.vm._isBeingDestroyed){remove(this.vm._watchers,this);}var i=this.deps.length;while(i--){this.deps[i].removeSub(this);}this.active=false;}};/*  */var sharedPropertyDefinition={enumerable:true,configurable:true,get:noop,set:noop};function proxy(target,sourceKey,key){sharedPropertyDefinition.get=function proxyGetter(){return this[sourceKey][key];};sharedPropertyDefinition.set=function proxySetter(val){this[sourceKey][key]=val;};Object.defineProperty(target,key,sharedPropertyDefinition);}function initState(vm){vm._watchers=[];var opts=vm.$options;if(opts.props){initProps(vm,opts.props);}if(opts.methods){initMethods(vm,opts.methods);}if(opts.data){initData(vm);}else{observe(vm._data={},true/* asRootData */);}if(opts.computed){initComputed(vm,opts.computed);}if(opts.watch&&opts.watch!==nativeWatch){initWatch(vm,opts.watch);}}function initProps(vm,propsOptions){var propsData=vm.$options.propsData||{};var props=vm._props={};// cache prop keys so that future props updates can iterate using Array
// instead of dynamic object key enumeration.
var keys=vm.$options._propKeys=[];var isRoot=!vm.$parent;// root instance props should be converted
if(!isRoot){toggleObserving(false);}var loop=function loop(key){keys.push(key);var value=validateProp(key,propsOptions,propsData,vm);/* istanbul ignore else */{defineReactive$$1(props,key,value);}// static props are already proxied on the component's prototype
// during Vue.extend(). We only need to proxy props defined at
// instantiation here.
if(!(key in vm)){proxy(vm,"_props",key);}};for(var key in propsOptions){loop(key);}toggleObserving(true);}function initData(vm){var data=vm.$options.data;data=vm._data=typeof data==='function'?getData(data,vm):data||{};if(!isPlainObject(data)){data={};}// proxy data on instance
var keys=Object.keys(data);var props=vm.$options.props;var methods=vm.$options.methods;var i=keys.length;while(i--){var key=keys[i];if(props&&hasOwn(props,key));else if(!isReserved(key)){proxy(vm,"_data",key);}}// observe data
observe(data,true/* asRootData */);}function getData(data,vm){// #7573 disable dep collection when invoking data getters
pushTarget();try{return data.call(vm,vm);}catch(e){handleError(e,vm,"data()");return{};}finally{popTarget();}}var computedWatcherOptions={lazy:true};function initComputed(vm,computed){// $flow-disable-line
var watchers=vm._computedWatchers=Object.create(null);// computed properties are just getters during SSR
var isSSR=isServerRendering();for(var key in computed){var userDef=computed[key];var getter=typeof userDef==='function'?userDef:userDef.get;if(!isSSR){// create internal watcher for the computed property.
watchers[key]=new Watcher(vm,getter||noop,noop,computedWatcherOptions);}// component-defined computed properties are already defined on the
// component prototype. We only need to define computed properties defined
// at instantiation here.
if(!(key in vm)){defineComputed(vm,key,userDef);}}}function defineComputed(target,key,userDef){var shouldCache=!isServerRendering();if(typeof userDef==='function'){sharedPropertyDefinition.get=shouldCache?createComputedGetter(key):createGetterInvoker(userDef);sharedPropertyDefinition.set=noop;}else{sharedPropertyDefinition.get=userDef.get?shouldCache&&userDef.cache!==false?createComputedGetter(key):createGetterInvoker(userDef.get):noop;sharedPropertyDefinition.set=userDef.set||noop;}Object.defineProperty(target,key,sharedPropertyDefinition);}function createComputedGetter(key){return function computedGetter(){var watcher=this._computedWatchers&&this._computedWatchers[key];if(watcher){if(watcher.dirty){watcher.evaluate();}if(Dep.target){watcher.depend();}return watcher.value;}};}function createGetterInvoker(fn){return function computedGetter(){return fn.call(this,this);};}function initMethods(vm,methods){var props=vm.$options.props;for(var key in methods){vm[key]=typeof methods[key]!=='function'?noop:bind(methods[key],vm);}}function initWatch(vm,watch){for(var key in watch){var handler=watch[key];if(Array.isArray(handler)){for(var i=0;i<handler.length;i++){createWatcher(vm,key,handler[i]);}}else{createWatcher(vm,key,handler);}}}function createWatcher(vm,expOrFn,handler,options){if(isPlainObject(handler)){options=handler;handler=handler.handler;}if(typeof handler==='string'){handler=vm[handler];}return vm.$watch(expOrFn,handler,options);}function stateMixin(Vue){// flow somehow has problems with directly declared definition object
// when using Object.defineProperty, so we have to procedurally build up
// the object here.
var dataDef={};dataDef.get=function(){return this._data;};var propsDef={};propsDef.get=function(){return this._props;};Object.defineProperty(Vue.prototype,'$data',dataDef);Object.defineProperty(Vue.prototype,'$props',propsDef);Vue.prototype.$set=set;Vue.prototype.$delete=del;Vue.prototype.$watch=function(expOrFn,cb,options){var vm=this;if(isPlainObject(cb)){return createWatcher(vm,expOrFn,cb,options);}options=options||{};options.user=true;var watcher=new Watcher(vm,expOrFn,cb,options);if(options.immediate){try{cb.call(vm,watcher.value);}catch(error){handleError(error,vm,"callback for immediate watcher \""+watcher.expression+"\"");}}return function unwatchFn(){watcher.teardown();};};}/*  */var uid$3=0;function initMixin(Vue){Vue.prototype._init=function(options){var vm=this;// a uid
vm._uid=uid$3++;// a flag to avoid this being observed
vm._isVue=true;// merge options
if(options&&options._isComponent){// optimize internal component instantiation
// since dynamic options merging is pretty slow, and none of the
// internal component options needs special treatment.
initInternalComponent(vm,options);}else{vm.$options=mergeOptions(resolveConstructorOptions(vm.constructor),options||{},vm);}/* istanbul ignore else */{vm._renderProxy=vm;}// expose real self
vm._self=vm;initLifecycle(vm);initEvents(vm);initRender(vm);callHook(vm,'beforeCreate');initInjections(vm);// resolve injections before data/props
initState(vm);initProvide(vm);// resolve provide after data/props
callHook(vm,'created');if(vm.$options.el){vm.$mount(vm.$options.el);}};}function initInternalComponent(vm,options){var opts=vm.$options=Object.create(vm.constructor.options);// doing this because it's faster than dynamic enumeration.
var parentVnode=options._parentVnode;opts.parent=options.parent;opts._parentVnode=parentVnode;var vnodeComponentOptions=parentVnode.componentOptions;opts.propsData=vnodeComponentOptions.propsData;opts._parentListeners=vnodeComponentOptions.listeners;opts._renderChildren=vnodeComponentOptions.children;opts._componentTag=vnodeComponentOptions.tag;if(options.render){opts.render=options.render;opts.staticRenderFns=options.staticRenderFns;}}function resolveConstructorOptions(Ctor){var options=Ctor.options;if(Ctor["super"]){var superOptions=resolveConstructorOptions(Ctor["super"]);var cachedSuperOptions=Ctor.superOptions;if(superOptions!==cachedSuperOptions){// super option changed,
// need to resolve new options.
Ctor.superOptions=superOptions;// check if there are any late-modified/attached options (#4976)
var modifiedOptions=resolveModifiedOptions(Ctor);// update base extend options
if(modifiedOptions){extend(Ctor.extendOptions,modifiedOptions);}options=Ctor.options=mergeOptions(superOptions,Ctor.extendOptions);if(options.name){options.components[options.name]=Ctor;}}}return options;}function resolveModifiedOptions(Ctor){var modified;var latest=Ctor.options;var sealed=Ctor.sealedOptions;for(var key in latest){if(latest[key]!==sealed[key]){if(!modified){modified={};}modified[key]=latest[key];}}return modified;}function Vue(options){this._init(options);}initMixin(Vue);stateMixin(Vue);eventsMixin(Vue);lifecycleMixin(Vue);renderMixin(Vue);/*  */function initUse(Vue){Vue.use=function(plugin){var installedPlugins=this._installedPlugins||(this._installedPlugins=[]);if(installedPlugins.indexOf(plugin)>-1){return this;}// additional parameters
var args=toArray(arguments,1);args.unshift(this);if(typeof plugin.install==='function'){plugin.install.apply(plugin,args);}else if(typeof plugin==='function'){plugin.apply(null,args);}installedPlugins.push(plugin);return this;};}/*  */function initMixin$1(Vue){Vue.mixin=function(mixin){this.options=mergeOptions(this.options,mixin);return this;};}/*  */function initExtend(Vue){/**
     * Each instance constructor, including Vue, has a unique
     * cid. This enables us to create wrapped "child
     * constructors" for prototypal inheritance and cache them.
     */Vue.cid=0;var cid=1;/**
     * Class inheritance
     */Vue.extend=function(extendOptions){extendOptions=extendOptions||{};var Super=this;var SuperId=Super.cid;var cachedCtors=extendOptions._Ctor||(extendOptions._Ctor={});if(cachedCtors[SuperId]){return cachedCtors[SuperId];}var name=extendOptions.name||Super.options.name;var Sub=function VueComponent(options){this._init(options);};Sub.prototype=Object.create(Super.prototype);Sub.prototype.constructor=Sub;Sub.cid=cid++;Sub.options=mergeOptions(Super.options,extendOptions);Sub['super']=Super;// For props and computed properties, we define the proxy getters on
// the Vue instances at extension time, on the extended prototype. This
// avoids Object.defineProperty calls for each instance created.
if(Sub.options.props){initProps$1(Sub);}if(Sub.options.computed){initComputed$1(Sub);}// allow further extension/mixin/plugin usage
Sub.extend=Super.extend;Sub.mixin=Super.mixin;Sub.use=Super.use;// create asset registers, so extended classes
// can have their private assets too.
ASSET_TYPES.forEach(function(type){Sub[type]=Super[type];});// enable recursive self-lookup
if(name){Sub.options.components[name]=Sub;}// keep a reference to the super options at extension time.
// later at instantiation we can check if Super's options have
// been updated.
Sub.superOptions=Super.options;Sub.extendOptions=extendOptions;Sub.sealedOptions=extend({},Sub.options);// cache constructor
cachedCtors[SuperId]=Sub;return Sub;};}function initProps$1(Comp){var props=Comp.options.props;for(var key in props){proxy(Comp.prototype,"_props",key);}}function initComputed$1(Comp){var computed=Comp.options.computed;for(var key in computed){defineComputed(Comp.prototype,key,computed[key]);}}/*  */function initAssetRegisters(Vue){/**
     * Create asset registration methods.
     */ASSET_TYPES.forEach(function(type){Vue[type]=function(id,definition){if(!definition){return this.options[type+'s'][id];}else{if(type==='component'&&isPlainObject(definition)){definition.name=definition.name||id;definition=this.options._base.extend(definition);}if(type==='directive'&&typeof definition==='function'){definition={bind:definition,update:definition};}this.options[type+'s'][id]=definition;return definition;}};});}/*  */function getComponentName(opts){return opts&&(opts.Ctor.options.name||opts.tag);}function matches(pattern,name){if(Array.isArray(pattern)){return pattern.indexOf(name)>-1;}else if(typeof pattern==='string'){return pattern.split(',').indexOf(name)>-1;}else if(isRegExp(pattern)){return pattern.test(name);}/* istanbul ignore next */return false;}function pruneCache(keepAliveInstance,filter){var cache=keepAliveInstance.cache;var keys=keepAliveInstance.keys;var _vnode=keepAliveInstance._vnode;for(var key in cache){var cachedNode=cache[key];if(cachedNode){var name=getComponentName(cachedNode.componentOptions);if(name&&!filter(name)){pruneCacheEntry(cache,key,keys,_vnode);}}}}function pruneCacheEntry(cache,key,keys,current){var cached$$1=cache[key];if(cached$$1&&(!current||cached$$1.tag!==current.tag)){cached$$1.componentInstance.$destroy();}cache[key]=null;remove(keys,key);}var patternTypes=[String,RegExp,Array];var KeepAlive={name:'keep-alive',"abstract":true,props:{include:patternTypes,exclude:patternTypes,max:[String,Number]},created:function created(){this.cache=Object.create(null);this.keys=[];},destroyed:function destroyed(){for(var key in this.cache){pruneCacheEntry(this.cache,key,this.keys);}},mounted:function mounted(){var this$1=this;this.$watch('include',function(val){pruneCache(this$1,function(name){return matches(val,name);});});this.$watch('exclude',function(val){pruneCache(this$1,function(name){return!matches(val,name);});});},render:function render(){var slot=this.$slots["default"];var vnode=getFirstComponentChild(slot);var componentOptions=vnode&&vnode.componentOptions;if(componentOptions){// check pattern
var name=getComponentName(componentOptions);var ref=this;var include=ref.include;var exclude=ref.exclude;if(// not included
include&&(!name||!matches(include,name))||// excluded
exclude&&name&&matches(exclude,name)){return vnode;}var ref$1=this;var cache=ref$1.cache;var keys=ref$1.keys;var key=vnode.key==null// same constructor may get registered as different local components
// so cid alone is not enough (#3269)
?componentOptions.Ctor.cid+(componentOptions.tag?"::"+componentOptions.tag:''):vnode.key;if(cache[key]){vnode.componentInstance=cache[key].componentInstance;// make current key freshest
remove(keys,key);keys.push(key);}else{cache[key]=vnode;keys.push(key);// prune oldest entry
if(this.max&&keys.length>parseInt(this.max)){pruneCacheEntry(cache,keys[0],keys,this._vnode);}}vnode.data.keepAlive=true;}return vnode||slot&&slot[0];}};var builtInComponents={KeepAlive:KeepAlive};/*  */function initGlobalAPI(Vue){// config
var configDef={};configDef.get=function(){return config;};Object.defineProperty(Vue,'config',configDef);// exposed util methods.
// NOTE: these are not considered part of the public API - avoid relying on
// them unless you are aware of the risk.
Vue.util={warn:warn,extend:extend,mergeOptions:mergeOptions,defineReactive:defineReactive$$1};Vue.set=set;Vue["delete"]=del;Vue.nextTick=nextTick;// 2.6 explicit observable API
Vue.observable=function(obj){observe(obj);return obj;};Vue.options=Object.create(null);ASSET_TYPES.forEach(function(type){Vue.options[type+'s']=Object.create(null);});// this is used to identify the "base" constructor to extend all plain-object
// components with in Weex's multi-instance scenarios.
Vue.options._base=Vue;extend(Vue.options.components,builtInComponents);initUse(Vue);initMixin$1(Vue);initExtend(Vue);initAssetRegisters(Vue);}initGlobalAPI(Vue);Object.defineProperty(Vue.prototype,'$isServer',{get:isServerRendering});Object.defineProperty(Vue.prototype,'$ssrContext',{get:function get(){/* istanbul ignore next */return this.$vnode&&this.$vnode.ssrContext;}});// expose FunctionalRenderContext for ssr runtime helper installation
Object.defineProperty(Vue,'FunctionalRenderContext',{value:FunctionalRenderContext});Vue.version='2.6.10';/*  */ // these are reserved for web because they are directly compiled away
// during template compilation
var isReservedAttr=makeMap('style,class');// attributes that should be using props for binding
var acceptValue=makeMap('input,textarea,option,select,progress');var mustUseProp=function mustUseProp(tag,type,attr){return attr==='value'&&acceptValue(tag)&&type!=='button'||attr==='selected'&&tag==='option'||attr==='checked'&&tag==='input'||attr==='muted'&&tag==='video';};var isEnumeratedAttr=makeMap('contenteditable,draggable,spellcheck');var isValidContentEditableValue=makeMap('events,caret,typing,plaintext-only');var convertEnumeratedValue=function convertEnumeratedValue(key,value){return isFalsyAttrValue(value)||value==='false'?'false'// allow arbitrary string value for contenteditable
:key==='contenteditable'&&isValidContentEditableValue(value)?value:'true';};var isBooleanAttr=makeMap('allowfullscreen,async,autofocus,autoplay,checked,compact,controls,declare,'+'default,defaultchecked,defaultmuted,defaultselected,defer,disabled,'+'enabled,formnovalidate,hidden,indeterminate,inert,ismap,itemscope,loop,multiple,'+'muted,nohref,noresize,noshade,novalidate,nowrap,open,pauseonexit,readonly,'+'required,reversed,scoped,seamless,selected,sortable,translate,'+'truespeed,typemustmatch,visible');var xlinkNS='http://www.w3.org/1999/xlink';var isXlink=function isXlink(name){return name.charAt(5)===':'&&name.slice(0,5)==='xlink';};var getXlinkProp=function getXlinkProp(name){return isXlink(name)?name.slice(6,name.length):'';};var isFalsyAttrValue=function isFalsyAttrValue(val){return val==null||val===false;};/*  */function genClassForVnode(vnode){var data=vnode.data;var parentNode=vnode;var childNode=vnode;while(isDef(childNode.componentInstance)){childNode=childNode.componentInstance._vnode;if(childNode&&childNode.data){data=mergeClassData(childNode.data,data);}}while(isDef(parentNode=parentNode.parent)){if(parentNode&&parentNode.data){data=mergeClassData(data,parentNode.data);}}return renderClass(data.staticClass,data["class"]);}function mergeClassData(child,parent){return{staticClass:concat(child.staticClass,parent.staticClass),"class":isDef(child["class"])?[child["class"],parent["class"]]:parent["class"]};}function renderClass(staticClass,dynamicClass){if(isDef(staticClass)||isDef(dynamicClass)){return concat(staticClass,stringifyClass(dynamicClass));}/* istanbul ignore next */return'';}function concat(a,b){return a?b?a+' '+b:a:b||'';}function stringifyClass(value){if(Array.isArray(value)){return stringifyArray(value);}if(isObject(value)){return stringifyObject(value);}if(typeof value==='string'){return value;}/* istanbul ignore next */return'';}function stringifyArray(value){var res='';var stringified;for(var i=0,l=value.length;i<l;i++){if(isDef(stringified=stringifyClass(value[i]))&&stringified!==''){if(res){res+=' ';}res+=stringified;}}return res;}function stringifyObject(value){var res='';for(var key in value){if(value[key]){if(res){res+=' ';}res+=key;}}return res;}/*  */var namespaceMap={svg:'http://www.w3.org/2000/svg',math:'http://www.w3.org/1998/Math/MathML'};var isHTMLTag=makeMap('html,body,base,head,link,meta,style,title,'+'address,article,aside,footer,header,h1,h2,h3,h4,h5,h6,hgroup,nav,section,'+'div,dd,dl,dt,figcaption,figure,picture,hr,img,li,main,ol,p,pre,ul,'+'a,b,abbr,bdi,bdo,br,cite,code,data,dfn,em,i,kbd,mark,q,rp,rt,rtc,ruby,'+'s,samp,small,span,strong,sub,sup,time,u,var,wbr,area,audio,map,track,video,'+'embed,object,param,source,canvas,script,noscript,del,ins,'+'caption,col,colgroup,table,thead,tbody,td,th,tr,'+'button,datalist,fieldset,form,input,label,legend,meter,optgroup,option,'+'output,progress,select,textarea,'+'details,dialog,menu,menuitem,summary,'+'content,element,shadow,template,blockquote,iframe,tfoot');// this map is intentionally selective, only covering SVG elements that may
// contain child elements.
var isSVG=makeMap('svg,animate,circle,clippath,cursor,defs,desc,ellipse,filter,font-face,'+'foreignObject,g,glyph,image,line,marker,mask,missing-glyph,path,pattern,'+'polygon,polyline,rect,switch,symbol,text,textpath,tspan,use,view',true);var isReservedTag=function isReservedTag(tag){return isHTMLTag(tag)||isSVG(tag);};function getTagNamespace(tag){if(isSVG(tag)){return'svg';}// basic support for MathML
// note it doesn't support other MathML elements being component roots
if(tag==='math'){return'math';}}var unknownElementCache=Object.create(null);function isUnknownElement(tag){/* istanbul ignore if */if(!inBrowser){return true;}if(isReservedTag(tag)){return false;}tag=tag.toLowerCase();/* istanbul ignore if */if(unknownElementCache[tag]!=null){return unknownElementCache[tag];}var el=document.createElement(tag);if(tag.indexOf('-')>-1){// http://stackoverflow.com/a/28210364/1070244
return unknownElementCache[tag]=el.constructor===window.HTMLUnknownElement||el.constructor===window.HTMLElement;}else{return unknownElementCache[tag]=/HTMLUnknownElement/.test(el.toString());}}var isTextInputType=makeMap('text,number,password,search,email,tel,url');/*  */ /**
   * Query an element selector if it's not an element already.
   */function query(el){if(typeof el==='string'){var selected=document.querySelector(el);if(!selected){return document.createElement('div');}return selected;}else{return el;}}/*  */function createElement$1(tagName,vnode){var elm=document.createElement(tagName);if(tagName!=='select'){return elm;}// false or null will remove the attribute but undefined will not
if(vnode.data&&vnode.data.attrs&&vnode.data.attrs.multiple!==undefined){elm.setAttribute('multiple','multiple');}return elm;}function createElementNS(namespace,tagName){return document.createElementNS(namespaceMap[namespace],tagName);}function createTextNode(text){return document.createTextNode(text);}function createComment(text){return document.createComment(text);}function insertBefore(parentNode,newNode,referenceNode){parentNode.insertBefore(newNode,referenceNode);}function removeChild(node,child){node.removeChild(child);}function appendChild(node,child){node.appendChild(child);}function parentNode(node){return node.parentNode;}function nextSibling(node){return node.nextSibling;}function tagName(node){return node.tagName;}function setTextContent(node,text){node.textContent=text;}function setStyleScope(node,scopeId){node.setAttribute(scopeId,'');}var nodeOps=/*#__PURE__*/Object.freeze({createElement:createElement$1,createElementNS:createElementNS,createTextNode:createTextNode,createComment:createComment,insertBefore:insertBefore,removeChild:removeChild,appendChild:appendChild,parentNode:parentNode,nextSibling:nextSibling,tagName:tagName,setTextContent:setTextContent,setStyleScope:setStyleScope});/*  */var ref={create:function create(_,vnode){registerRef(vnode);},update:function update(oldVnode,vnode){if(oldVnode.data.ref!==vnode.data.ref){registerRef(oldVnode,true);registerRef(vnode);}},destroy:function destroy(vnode){registerRef(vnode,true);}};function registerRef(vnode,isRemoval){var key=vnode.data.ref;if(!isDef(key)){return;}var vm=vnode.context;var ref=vnode.componentInstance||vnode.elm;var refs=vm.$refs;if(isRemoval){if(Array.isArray(refs[key])){remove(refs[key],ref);}else if(refs[key]===ref){refs[key]=undefined;}}else{if(vnode.data.refInFor){if(!Array.isArray(refs[key])){refs[key]=[ref];}else if(refs[key].indexOf(ref)<0){// $flow-disable-line
refs[key].push(ref);}}else{refs[key]=ref;}}}/**
   * Virtual DOM patching algorithm based on Snabbdom by
   * Simon Friis Vindum (@paldepind)
   * Licensed under the MIT License
   * https://github.com/paldepind/snabbdom/blob/master/LICENSE
   *
   * modified by Evan You (@yyx990803)
   *
   * Not type-checking this because this file is perf-critical and the cost
   * of making flow understand it is not worth it.
   */var emptyNode=new VNode('',{},[]);var hooks=['create','activate','update','remove','destroy'];function sameVnode(a,b){return a.key===b.key&&(a.tag===b.tag&&a.isComment===b.isComment&&isDef(a.data)===isDef(b.data)&&sameInputType(a,b)||isTrue(a.isAsyncPlaceholder)&&a.asyncFactory===b.asyncFactory&&isUndef(b.asyncFactory.error));}function sameInputType(a,b){if(a.tag!=='input'){return true;}var i;var typeA=isDef(i=a.data)&&isDef(i=i.attrs)&&i.type;var typeB=isDef(i=b.data)&&isDef(i=i.attrs)&&i.type;return typeA===typeB||isTextInputType(typeA)&&isTextInputType(typeB);}function createKeyToOldIdx(children,beginIdx,endIdx){var i,key;var map={};for(i=beginIdx;i<=endIdx;++i){key=children[i].key;if(isDef(key)){map[key]=i;}}return map;}function createPatchFunction(backend){var i,j;var cbs={};var modules=backend.modules;var nodeOps=backend.nodeOps;for(i=0;i<hooks.length;++i){cbs[hooks[i]]=[];for(j=0;j<modules.length;++j){if(isDef(modules[j][hooks[i]])){cbs[hooks[i]].push(modules[j][hooks[i]]);}}}function emptyNodeAt(elm){return new VNode(nodeOps.tagName(elm).toLowerCase(),{},[],undefined,elm);}function createRmCb(childElm,listeners){function remove$$1(){if(--remove$$1.listeners===0){removeNode(childElm);}}remove$$1.listeners=listeners;return remove$$1;}function removeNode(el){var parent=nodeOps.parentNode(el);// element may have already been removed due to v-html / v-text
if(isDef(parent)){nodeOps.removeChild(parent,el);}}function createElm(vnode,insertedVnodeQueue,parentElm,refElm,nested,ownerArray,index){if(isDef(vnode.elm)&&isDef(ownerArray)){// This vnode was used in a previous render!
// now it's used as a new node, overwriting its elm would cause
// potential patch errors down the road when it's used as an insertion
// reference node. Instead, we clone the node on-demand before creating
// associated DOM element for it.
vnode=ownerArray[index]=cloneVNode(vnode);}vnode.isRootInsert=!nested;// for transition enter check
if(createComponent(vnode,insertedVnodeQueue,parentElm,refElm)){return;}var data=vnode.data;var children=vnode.children;var tag=vnode.tag;if(isDef(tag)){vnode.elm=vnode.ns?nodeOps.createElementNS(vnode.ns,tag):nodeOps.createElement(tag,vnode);setScope(vnode);/* istanbul ignore if */{createChildren(vnode,children,insertedVnodeQueue);if(isDef(data)){invokeCreateHooks(vnode,insertedVnodeQueue);}insert(parentElm,vnode.elm,refElm);}}else if(isTrue(vnode.isComment)){vnode.elm=nodeOps.createComment(vnode.text);insert(parentElm,vnode.elm,refElm);}else{vnode.elm=nodeOps.createTextNode(vnode.text);insert(parentElm,vnode.elm,refElm);}}function createComponent(vnode,insertedVnodeQueue,parentElm,refElm){var i=vnode.data;if(isDef(i)){var isReactivated=isDef(vnode.componentInstance)&&i.keepAlive;if(isDef(i=i.hook)&&isDef(i=i.init)){i(vnode,false/* hydrating */);}// after calling the init hook, if the vnode is a child component
// it should've created a child instance and mounted it. the child
// component also has set the placeholder vnode's elm.
// in that case we can just return the element and be done.
if(isDef(vnode.componentInstance)){initComponent(vnode,insertedVnodeQueue);insert(parentElm,vnode.elm,refElm);if(isTrue(isReactivated)){reactivateComponent(vnode,insertedVnodeQueue,parentElm,refElm);}return true;}}}function initComponent(vnode,insertedVnodeQueue){if(isDef(vnode.data.pendingInsert)){insertedVnodeQueue.push.apply(insertedVnodeQueue,vnode.data.pendingInsert);vnode.data.pendingInsert=null;}vnode.elm=vnode.componentInstance.$el;if(isPatchable(vnode)){invokeCreateHooks(vnode,insertedVnodeQueue);setScope(vnode);}else{// empty component root.
// skip all element-related modules except for ref (#3455)
registerRef(vnode);// make sure to invoke the insert hook
insertedVnodeQueue.push(vnode);}}function reactivateComponent(vnode,insertedVnodeQueue,parentElm,refElm){var i;// hack for #4339: a reactivated component with inner transition
// does not trigger because the inner node's created hooks are not called
// again. It's not ideal to involve module-specific logic in here but
// there doesn't seem to be a better way to do it.
var innerNode=vnode;while(innerNode.componentInstance){innerNode=innerNode.componentInstance._vnode;if(isDef(i=innerNode.data)&&isDef(i=i.transition)){for(i=0;i<cbs.activate.length;++i){cbs.activate[i](emptyNode,innerNode);}insertedVnodeQueue.push(innerNode);break;}}// unlike a newly created component,
// a reactivated keep-alive component doesn't insert itself
insert(parentElm,vnode.elm,refElm);}function insert(parent,elm,ref$$1){if(isDef(parent)){if(isDef(ref$$1)){if(nodeOps.parentNode(ref$$1)===parent){nodeOps.insertBefore(parent,elm,ref$$1);}}else{nodeOps.appendChild(parent,elm);}}}function createChildren(vnode,children,insertedVnodeQueue){if(Array.isArray(children)){for(var i=0;i<children.length;++i){createElm(children[i],insertedVnodeQueue,vnode.elm,null,true,children,i);}}else if(isPrimitive(vnode.text)){nodeOps.appendChild(vnode.elm,nodeOps.createTextNode(String(vnode.text)));}}function isPatchable(vnode){while(vnode.componentInstance){vnode=vnode.componentInstance._vnode;}return isDef(vnode.tag);}function invokeCreateHooks(vnode,insertedVnodeQueue){for(var i$1=0;i$1<cbs.create.length;++i$1){cbs.create[i$1](emptyNode,vnode);}i=vnode.data.hook;// Reuse variable
if(isDef(i)){if(isDef(i.create)){i.create(emptyNode,vnode);}if(isDef(i.insert)){insertedVnodeQueue.push(vnode);}}}// set scope id attribute for scoped CSS.
// this is implemented as a special case to avoid the overhead
// of going through the normal attribute patching process.
function setScope(vnode){var i;if(isDef(i=vnode.fnScopeId)){nodeOps.setStyleScope(vnode.elm,i);}else{var ancestor=vnode;while(ancestor){if(isDef(i=ancestor.context)&&isDef(i=i.$options._scopeId)){nodeOps.setStyleScope(vnode.elm,i);}ancestor=ancestor.parent;}}// for slot content they should also get the scopeId from the host instance.
if(isDef(i=activeInstance)&&i!==vnode.context&&i!==vnode.fnContext&&isDef(i=i.$options._scopeId)){nodeOps.setStyleScope(vnode.elm,i);}}function addVnodes(parentElm,refElm,vnodes,startIdx,endIdx,insertedVnodeQueue){for(;startIdx<=endIdx;++startIdx){createElm(vnodes[startIdx],insertedVnodeQueue,parentElm,refElm,false,vnodes,startIdx);}}function invokeDestroyHook(vnode){var i,j;var data=vnode.data;if(isDef(data)){if(isDef(i=data.hook)&&isDef(i=i.destroy)){i(vnode);}for(i=0;i<cbs.destroy.length;++i){cbs.destroy[i](vnode);}}if(isDef(i=vnode.children)){for(j=0;j<vnode.children.length;++j){invokeDestroyHook(vnode.children[j]);}}}function removeVnodes(parentElm,vnodes,startIdx,endIdx){for(;startIdx<=endIdx;++startIdx){var ch=vnodes[startIdx];if(isDef(ch)){if(isDef(ch.tag)){removeAndInvokeRemoveHook(ch);invokeDestroyHook(ch);}else{// Text node
removeNode(ch.elm);}}}}function removeAndInvokeRemoveHook(vnode,rm){if(isDef(rm)||isDef(vnode.data)){var i;var listeners=cbs.remove.length+1;if(isDef(rm)){// we have a recursively passed down rm callback
// increase the listeners count
rm.listeners+=listeners;}else{// directly removing
rm=createRmCb(vnode.elm,listeners);}// recursively invoke hooks on child component root node
if(isDef(i=vnode.componentInstance)&&isDef(i=i._vnode)&&isDef(i.data)){removeAndInvokeRemoveHook(i,rm);}for(i=0;i<cbs.remove.length;++i){cbs.remove[i](vnode,rm);}if(isDef(i=vnode.data.hook)&&isDef(i=i.remove)){i(vnode,rm);}else{rm();}}else{removeNode(vnode.elm);}}function updateChildren(parentElm,oldCh,newCh,insertedVnodeQueue,removeOnly){var oldStartIdx=0;var newStartIdx=0;var oldEndIdx=oldCh.length-1;var oldStartVnode=oldCh[0];var oldEndVnode=oldCh[oldEndIdx];var newEndIdx=newCh.length-1;var newStartVnode=newCh[0];var newEndVnode=newCh[newEndIdx];var oldKeyToIdx,idxInOld,vnodeToMove,refElm;// removeOnly is a special flag used only by <transition-group>
// to ensure removed elements stay in correct relative positions
// during leaving transitions
var canMove=!removeOnly;while(oldStartIdx<=oldEndIdx&&newStartIdx<=newEndIdx){if(isUndef(oldStartVnode)){oldStartVnode=oldCh[++oldStartIdx];// Vnode has been moved left
}else if(isUndef(oldEndVnode)){oldEndVnode=oldCh[--oldEndIdx];}else if(sameVnode(oldStartVnode,newStartVnode)){patchVnode(oldStartVnode,newStartVnode,insertedVnodeQueue,newCh,newStartIdx);oldStartVnode=oldCh[++oldStartIdx];newStartVnode=newCh[++newStartIdx];}else if(sameVnode(oldEndVnode,newEndVnode)){patchVnode(oldEndVnode,newEndVnode,insertedVnodeQueue,newCh,newEndIdx);oldEndVnode=oldCh[--oldEndIdx];newEndVnode=newCh[--newEndIdx];}else if(sameVnode(oldStartVnode,newEndVnode)){// Vnode moved right
patchVnode(oldStartVnode,newEndVnode,insertedVnodeQueue,newCh,newEndIdx);canMove&&nodeOps.insertBefore(parentElm,oldStartVnode.elm,nodeOps.nextSibling(oldEndVnode.elm));oldStartVnode=oldCh[++oldStartIdx];newEndVnode=newCh[--newEndIdx];}else if(sameVnode(oldEndVnode,newStartVnode)){// Vnode moved left
patchVnode(oldEndVnode,newStartVnode,insertedVnodeQueue,newCh,newStartIdx);canMove&&nodeOps.insertBefore(parentElm,oldEndVnode.elm,oldStartVnode.elm);oldEndVnode=oldCh[--oldEndIdx];newStartVnode=newCh[++newStartIdx];}else{if(isUndef(oldKeyToIdx)){oldKeyToIdx=createKeyToOldIdx(oldCh,oldStartIdx,oldEndIdx);}idxInOld=isDef(newStartVnode.key)?oldKeyToIdx[newStartVnode.key]:findIdxInOld(newStartVnode,oldCh,oldStartIdx,oldEndIdx);if(isUndef(idxInOld)){// New element
createElm(newStartVnode,insertedVnodeQueue,parentElm,oldStartVnode.elm,false,newCh,newStartIdx);}else{vnodeToMove=oldCh[idxInOld];if(sameVnode(vnodeToMove,newStartVnode)){patchVnode(vnodeToMove,newStartVnode,insertedVnodeQueue,newCh,newStartIdx);oldCh[idxInOld]=undefined;canMove&&nodeOps.insertBefore(parentElm,vnodeToMove.elm,oldStartVnode.elm);}else{// same key but different element. treat as new element
createElm(newStartVnode,insertedVnodeQueue,parentElm,oldStartVnode.elm,false,newCh,newStartIdx);}}newStartVnode=newCh[++newStartIdx];}}if(oldStartIdx>oldEndIdx){refElm=isUndef(newCh[newEndIdx+1])?null:newCh[newEndIdx+1].elm;addVnodes(parentElm,refElm,newCh,newStartIdx,newEndIdx,insertedVnodeQueue);}else if(newStartIdx>newEndIdx){removeVnodes(parentElm,oldCh,oldStartIdx,oldEndIdx);}}function findIdxInOld(node,oldCh,start,end){for(var i=start;i<end;i++){var c=oldCh[i];if(isDef(c)&&sameVnode(node,c)){return i;}}}function patchVnode(oldVnode,vnode,insertedVnodeQueue,ownerArray,index,removeOnly){if(oldVnode===vnode){return;}if(isDef(vnode.elm)&&isDef(ownerArray)){// clone reused vnode
vnode=ownerArray[index]=cloneVNode(vnode);}var elm=vnode.elm=oldVnode.elm;if(isTrue(oldVnode.isAsyncPlaceholder)){if(isDef(vnode.asyncFactory.resolved)){hydrate(oldVnode.elm,vnode,insertedVnodeQueue);}else{vnode.isAsyncPlaceholder=true;}return;}// reuse element for static trees.
// note we only do this if the vnode is cloned -
// if the new node is not cloned it means the render functions have been
// reset by the hot-reload-api and we need to do a proper re-render.
if(isTrue(vnode.isStatic)&&isTrue(oldVnode.isStatic)&&vnode.key===oldVnode.key&&(isTrue(vnode.isCloned)||isTrue(vnode.isOnce))){vnode.componentInstance=oldVnode.componentInstance;return;}var i;var data=vnode.data;if(isDef(data)&&isDef(i=data.hook)&&isDef(i=i.prepatch)){i(oldVnode,vnode);}var oldCh=oldVnode.children;var ch=vnode.children;if(isDef(data)&&isPatchable(vnode)){for(i=0;i<cbs.update.length;++i){cbs.update[i](oldVnode,vnode);}if(isDef(i=data.hook)&&isDef(i=i.update)){i(oldVnode,vnode);}}if(isUndef(vnode.text)){if(isDef(oldCh)&&isDef(ch)){if(oldCh!==ch){updateChildren(elm,oldCh,ch,insertedVnodeQueue,removeOnly);}}else if(isDef(ch)){if(isDef(oldVnode.text)){nodeOps.setTextContent(elm,'');}addVnodes(elm,null,ch,0,ch.length-1,insertedVnodeQueue);}else if(isDef(oldCh)){removeVnodes(elm,oldCh,0,oldCh.length-1);}else if(isDef(oldVnode.text)){nodeOps.setTextContent(elm,'');}}else if(oldVnode.text!==vnode.text){nodeOps.setTextContent(elm,vnode.text);}if(isDef(data)){if(isDef(i=data.hook)&&isDef(i=i.postpatch)){i(oldVnode,vnode);}}}function invokeInsertHook(vnode,queue,initial){// delay insert hooks for component root nodes, invoke them after the
// element is really inserted
if(isTrue(initial)&&isDef(vnode.parent)){vnode.parent.data.pendingInsert=queue;}else{for(var i=0;i<queue.length;++i){queue[i].data.hook.insert(queue[i]);}}}// list of modules that can skip create hook during hydration because they
// are already rendered on the client or has no need for initialization
// Note: style is excluded because it relies on initial clone for future
// deep updates (#7063).
var isRenderedModule=makeMap('attrs,class,staticClass,staticStyle,key');// Note: this is a browser-only function so we can assume elms are DOM nodes.
function hydrate(elm,vnode,insertedVnodeQueue,inVPre){var i;var tag=vnode.tag;var data=vnode.data;var children=vnode.children;inVPre=inVPre||data&&data.pre;vnode.elm=elm;if(isTrue(vnode.isComment)&&isDef(vnode.asyncFactory)){vnode.isAsyncPlaceholder=true;return true;}if(isDef(data)){if(isDef(i=data.hook)&&isDef(i=i.init)){i(vnode,true/* hydrating */);}if(isDef(i=vnode.componentInstance)){// child component. it should have hydrated its own tree.
initComponent(vnode,insertedVnodeQueue);return true;}}if(isDef(tag)){if(isDef(children)){// empty element, allow client to pick up and populate children
if(!elm.hasChildNodes()){createChildren(vnode,children,insertedVnodeQueue);}else{// v-html and domProps: innerHTML
if(isDef(i=data)&&isDef(i=i.domProps)&&isDef(i=i.innerHTML)){if(i!==elm.innerHTML){return false;}}else{// iterate and compare children lists
var childrenMatch=true;var childNode=elm.firstChild;for(var i$1=0;i$1<children.length;i$1++){if(!childNode||!hydrate(childNode,children[i$1],insertedVnodeQueue,inVPre)){childrenMatch=false;break;}childNode=childNode.nextSibling;}// if childNode is not null, it means the actual childNodes list is
// longer than the virtual children list.
if(!childrenMatch||childNode){return false;}}}}if(isDef(data)){var fullInvoke=false;for(var key in data){if(!isRenderedModule(key)){fullInvoke=true;invokeCreateHooks(vnode,insertedVnodeQueue);break;}}if(!fullInvoke&&data['class']){// ensure collecting deps for deep class bindings for future updates
traverse(data['class']);}}}else if(elm.data!==vnode.text){elm.data=vnode.text;}return true;}return function patch(oldVnode,vnode,hydrating,removeOnly){if(isUndef(vnode)){if(isDef(oldVnode)){invokeDestroyHook(oldVnode);}return;}var isInitialPatch=false;var insertedVnodeQueue=[];if(isUndef(oldVnode)){// empty mount (likely as component), create new root element
isInitialPatch=true;createElm(vnode,insertedVnodeQueue);}else{var isRealElement=isDef(oldVnode.nodeType);if(!isRealElement&&sameVnode(oldVnode,vnode)){// patch existing root node
patchVnode(oldVnode,vnode,insertedVnodeQueue,null,null,removeOnly);}else{if(isRealElement){// mounting to a real element
// check if this is server-rendered content and if we can perform
// a successful hydration.
if(oldVnode.nodeType===1&&oldVnode.hasAttribute(SSR_ATTR)){oldVnode.removeAttribute(SSR_ATTR);hydrating=true;}if(isTrue(hydrating)){if(hydrate(oldVnode,vnode,insertedVnodeQueue)){invokeInsertHook(vnode,insertedVnodeQueue,true);return oldVnode;}}// either not server-rendered, or hydration failed.
// create an empty node and replace it
oldVnode=emptyNodeAt(oldVnode);}// replacing existing element
var oldElm=oldVnode.elm;var parentElm=nodeOps.parentNode(oldElm);// create new node
createElm(vnode,insertedVnodeQueue,// extremely rare edge case: do not insert if old element is in a
// leaving transition. Only happens when combining transition +
// keep-alive + HOCs. (#4590)
oldElm._leaveCb?null:parentElm,nodeOps.nextSibling(oldElm));// update parent placeholder node element, recursively
if(isDef(vnode.parent)){var ancestor=vnode.parent;var patchable=isPatchable(vnode);while(ancestor){for(var i=0;i<cbs.destroy.length;++i){cbs.destroy[i](ancestor);}ancestor.elm=vnode.elm;if(patchable){for(var i$1=0;i$1<cbs.create.length;++i$1){cbs.create[i$1](emptyNode,ancestor);}// #6513
// invoke insert hooks that may have been merged by create hooks.
// e.g. for directives that uses the "inserted" hook.
var insert=ancestor.data.hook.insert;if(insert.merged){// start at index 1 to avoid re-invoking component mounted hook
for(var i$2=1;i$2<insert.fns.length;i$2++){insert.fns[i$2]();}}}else{registerRef(ancestor);}ancestor=ancestor.parent;}}// destroy old node
if(isDef(parentElm)){removeVnodes(parentElm,[oldVnode],0,0);}else if(isDef(oldVnode.tag)){invokeDestroyHook(oldVnode);}}}invokeInsertHook(vnode,insertedVnodeQueue,isInitialPatch);return vnode.elm;};}/*  */var directives={create:updateDirectives,update:updateDirectives,destroy:function unbindDirectives(vnode){updateDirectives(vnode,emptyNode);}};function updateDirectives(oldVnode,vnode){if(oldVnode.data.directives||vnode.data.directives){_update(oldVnode,vnode);}}function _update(oldVnode,vnode){var isCreate=oldVnode===emptyNode;var isDestroy=vnode===emptyNode;var oldDirs=normalizeDirectives$1(oldVnode.data.directives,oldVnode.context);var newDirs=normalizeDirectives$1(vnode.data.directives,vnode.context);var dirsWithInsert=[];var dirsWithPostpatch=[];var key,oldDir,dir;for(key in newDirs){oldDir=oldDirs[key];dir=newDirs[key];if(!oldDir){// new directive, bind
callHook$1(dir,'bind',vnode,oldVnode);if(dir.def&&dir.def.inserted){dirsWithInsert.push(dir);}}else{// existing directive, update
dir.oldValue=oldDir.value;dir.oldArg=oldDir.arg;callHook$1(dir,'update',vnode,oldVnode);if(dir.def&&dir.def.componentUpdated){dirsWithPostpatch.push(dir);}}}if(dirsWithInsert.length){var callInsert=function callInsert(){for(var i=0;i<dirsWithInsert.length;i++){callHook$1(dirsWithInsert[i],'inserted',vnode,oldVnode);}};if(isCreate){mergeVNodeHook(vnode,'insert',callInsert);}else{callInsert();}}if(dirsWithPostpatch.length){mergeVNodeHook(vnode,'postpatch',function(){for(var i=0;i<dirsWithPostpatch.length;i++){callHook$1(dirsWithPostpatch[i],'componentUpdated',vnode,oldVnode);}});}if(!isCreate){for(key in oldDirs){if(!newDirs[key]){// no longer present, unbind
callHook$1(oldDirs[key],'unbind',oldVnode,oldVnode,isDestroy);}}}}var emptyModifiers=Object.create(null);function normalizeDirectives$1(dirs,vm){var res=Object.create(null);if(!dirs){// $flow-disable-line
return res;}var i,dir;for(i=0;i<dirs.length;i++){dir=dirs[i];if(!dir.modifiers){// $flow-disable-line
dir.modifiers=emptyModifiers;}res[getRawDirName(dir)]=dir;dir.def=resolveAsset(vm.$options,'directives',dir.name);}// $flow-disable-line
return res;}function getRawDirName(dir){return dir.rawName||dir.name+"."+Object.keys(dir.modifiers||{}).join('.');}function callHook$1(dir,hook,vnode,oldVnode,isDestroy){var fn=dir.def&&dir.def[hook];if(fn){try{fn(vnode.elm,dir,vnode,oldVnode,isDestroy);}catch(e){handleError(e,vnode.context,"directive "+dir.name+" "+hook+" hook");}}}var baseModules=[ref,directives];/*  */function updateAttrs(oldVnode,vnode){var opts=vnode.componentOptions;if(isDef(opts)&&opts.Ctor.options.inheritAttrs===false){return;}if(isUndef(oldVnode.data.attrs)&&isUndef(vnode.data.attrs)){return;}var key,cur,old;var elm=vnode.elm;var oldAttrs=oldVnode.data.attrs||{};var attrs=vnode.data.attrs||{};// clone observed objects, as the user probably wants to mutate it
if(isDef(attrs.__ob__)){attrs=vnode.data.attrs=extend({},attrs);}for(key in attrs){cur=attrs[key];old=oldAttrs[key];if(old!==cur){setAttr(elm,key,cur);}}// #4391: in IE9, setting type can reset value for input[type=radio]
// #6666: IE/Edge forces progress value down to 1 before setting a max
/* istanbul ignore if */if((isIE||isEdge)&&attrs.value!==oldAttrs.value){setAttr(elm,'value',attrs.value);}for(key in oldAttrs){if(isUndef(attrs[key])){if(isXlink(key)){elm.removeAttributeNS(xlinkNS,getXlinkProp(key));}else if(!isEnumeratedAttr(key)){elm.removeAttribute(key);}}}}function setAttr(el,key,value){if(el.tagName.indexOf('-')>-1){baseSetAttr(el,key,value);}else if(isBooleanAttr(key)){// set attribute for blank value
// e.g. <option disabled>Select one</option>
if(isFalsyAttrValue(value)){el.removeAttribute(key);}else{// technically allowfullscreen is a boolean attribute for <iframe>,
// but Flash expects a value of "true" when used on <embed> tag
value=key==='allowfullscreen'&&el.tagName==='EMBED'?'true':key;el.setAttribute(key,value);}}else if(isEnumeratedAttr(key)){el.setAttribute(key,convertEnumeratedValue(key,value));}else if(isXlink(key)){if(isFalsyAttrValue(value)){el.removeAttributeNS(xlinkNS,getXlinkProp(key));}else{el.setAttributeNS(xlinkNS,key,value);}}else{baseSetAttr(el,key,value);}}function baseSetAttr(el,key,value){if(isFalsyAttrValue(value)){el.removeAttribute(key);}else{// #7138: IE10 & 11 fires input event when setting placeholder on
// <textarea>... block the first input event and remove the blocker
// immediately.
/* istanbul ignore if */if(isIE&&!isIE9&&el.tagName==='TEXTAREA'&&key==='placeholder'&&value!==''&&!el.__ieph){var blocker=function blocker(e){e.stopImmediatePropagation();el.removeEventListener('input',blocker);};el.addEventListener('input',blocker);// $flow-disable-line
el.__ieph=true;/* IE placeholder patched */}el.setAttribute(key,value);}}var attrs={create:updateAttrs,update:updateAttrs};/*  */function updateClass(oldVnode,vnode){var el=vnode.elm;var data=vnode.data;var oldData=oldVnode.data;if(isUndef(data.staticClass)&&isUndef(data["class"])&&(isUndef(oldData)||isUndef(oldData.staticClass)&&isUndef(oldData["class"]))){return;}var cls=genClassForVnode(vnode);// handle transition classes
var transitionClass=el._transitionClasses;if(isDef(transitionClass)){cls=concat(cls,stringifyClass(transitionClass));}// set the class
if(cls!==el._prevClass){el.setAttribute('class',cls);el._prevClass=cls;}}var klass={create:updateClass,update:updateClass};/*  */ /*  */ /*  */ /*  */ // in some cases, the event used has to be determined at runtime
// so we used some reserved tokens during compile.
var RANGE_TOKEN='__r';var CHECKBOX_RADIO_TOKEN='__c';/*  */ // normalize v-model event tokens that can only be determined at runtime.
// it's important to place the event as the first in the array because
// the whole point is ensuring the v-model callback gets called before
// user-attached handlers.
function normalizeEvents(on){/* istanbul ignore if */if(isDef(on[RANGE_TOKEN])){// IE input[type=range] only supports `change` event
var event=isIE?'change':'input';on[event]=[].concat(on[RANGE_TOKEN],on[event]||[]);delete on[RANGE_TOKEN];}// This was originally intended to fix #4521 but no longer necessary
// after 2.5. Keeping it for backwards compat with generated code from < 2.4
/* istanbul ignore if */if(isDef(on[CHECKBOX_RADIO_TOKEN])){on.change=[].concat(on[CHECKBOX_RADIO_TOKEN],on.change||[]);delete on[CHECKBOX_RADIO_TOKEN];}}var target$1;function createOnceHandler$1(event,handler,capture){var _target=target$1;// save current target element in closure
return function onceHandler(){var res=handler.apply(null,arguments);if(res!==null){remove$2(event,onceHandler,capture,_target);}};}// #9446: Firefox <= 53 (in particular, ESR 52) has incorrect Event.timeStamp
// implementation and does not fire microtasks in between event propagation, so
// safe to exclude.
var useMicrotaskFix=isUsingMicroTask&&!(isFF&&Number(isFF[1])<=53);function add$1(name,handler,capture,passive){// async edge case #6566: inner click event triggers patch, event handler
// attached to outer element during patch, and triggered again. This
// happens because browsers fire microtask ticks between event propagation.
// the solution is simple: we save the timestamp when a handler is attached,
// and the handler would only fire if the event passed to it was fired
// AFTER it was attached.
if(useMicrotaskFix){var attachedTimestamp=currentFlushTimestamp;var original=handler;handler=original._wrapper=function(e){if(// no bubbling, should always fire.
// this is just a safety net in case event.timeStamp is unreliable in
// certain weird environments...
e.target===e.currentTarget||// event is fired after handler attachment
e.timeStamp>=attachedTimestamp||// bail for environments that have buggy event.timeStamp implementations
// #9462 iOS 9 bug: event.timeStamp is 0 after history.pushState
// #9681 QtWebEngine event.timeStamp is negative value
e.timeStamp<=0||// #9448 bail if event is fired in another document in a multi-page
// electron/nw.js app, since event.timeStamp will be using a different
// starting reference
e.target.ownerDocument!==document){return original.apply(this,arguments);}};}target$1.addEventListener(name,handler,supportsPassive?{capture:capture,passive:passive}:capture);}function remove$2(name,handler,capture,_target){(_target||target$1).removeEventListener(name,handler._wrapper||handler,capture);}function updateDOMListeners(oldVnode,vnode){if(isUndef(oldVnode.data.on)&&isUndef(vnode.data.on)){return;}var on=vnode.data.on||{};var oldOn=oldVnode.data.on||{};target$1=vnode.elm;normalizeEvents(on);updateListeners(on,oldOn,add$1,remove$2,createOnceHandler$1,vnode.context);target$1=undefined;}var events={create:updateDOMListeners,update:updateDOMListeners};/*  */var svgContainer;function updateDOMProps(oldVnode,vnode){if(isUndef(oldVnode.data.domProps)&&isUndef(vnode.data.domProps)){return;}var key,cur;var elm=vnode.elm;var oldProps=oldVnode.data.domProps||{};var props=vnode.data.domProps||{};// clone observed objects, as the user probably wants to mutate it
if(isDef(props.__ob__)){props=vnode.data.domProps=extend({},props);}for(key in oldProps){if(!(key in props)){elm[key]='';}}for(key in props){cur=props[key];// ignore children if the node has textContent or innerHTML,
// as these will throw away existing DOM nodes and cause removal errors
// on subsequent patches (#3360)
if(key==='textContent'||key==='innerHTML'){if(vnode.children){vnode.children.length=0;}if(cur===oldProps[key]){continue;}// #6601 work around Chrome version <= 55 bug where single textNode
// replaced by innerHTML/textContent retains its parentNode property
if(elm.childNodes.length===1){elm.removeChild(elm.childNodes[0]);}}if(key==='value'&&elm.tagName!=='PROGRESS'){// store value as _value as well since
// non-string values will be stringified
elm._value=cur;// avoid resetting cursor position when value is the same
var strCur=isUndef(cur)?'':String(cur);if(shouldUpdateValue(elm,strCur)){elm.value=strCur;}}else if(key==='innerHTML'&&isSVG(elm.tagName)&&isUndef(elm.innerHTML)){// IE doesn't support innerHTML for SVG elements
svgContainer=svgContainer||document.createElement('div');svgContainer.innerHTML="<svg>"+cur+"</svg>";var svg=svgContainer.firstChild;while(elm.firstChild){elm.removeChild(elm.firstChild);}while(svg.firstChild){elm.appendChild(svg.firstChild);}}else if(// skip the update if old and new VDOM state is the same.
// `value` is handled separately because the DOM value may be temporarily
// out of sync with VDOM state due to focus, composition and modifiers.
// This  #4521 by skipping the unnecesarry `checked` update.
cur!==oldProps[key]){// some property updates can throw
// e.g. `value` on <progress> w/ non-finite value
try{elm[key]=cur;}catch(e){}}}}// check platforms/web/util/attrs.js acceptValue
function shouldUpdateValue(elm,checkVal){return!elm.composing&&(elm.tagName==='OPTION'||isNotInFocusAndDirty(elm,checkVal)||isDirtyWithModifiers(elm,checkVal));}function isNotInFocusAndDirty(elm,checkVal){// return true when textbox (.number and .trim) loses focus and its value is
// not equal to the updated value
var notInFocus=true;// #6157
// work around IE bug when accessing document.activeElement in an iframe
try{notInFocus=document.activeElement!==elm;}catch(e){}return notInFocus&&elm.value!==checkVal;}function isDirtyWithModifiers(elm,newVal){var value=elm.value;var modifiers=elm._vModifiers;// injected by v-model runtime
if(isDef(modifiers)){if(modifiers.number){return toNumber(value)!==toNumber(newVal);}if(modifiers.trim){return value.trim()!==newVal.trim();}}return value!==newVal;}var domProps={create:updateDOMProps,update:updateDOMProps};/*  */var parseStyleText=cached(function(cssText){var res={};var listDelimiter=/;(?![^(]*\))/g;var propertyDelimiter=/:(.+)/;cssText.split(listDelimiter).forEach(function(item){if(item){var tmp=item.split(propertyDelimiter);tmp.length>1&&(res[tmp[0].trim()]=tmp[1].trim());}});return res;});// merge static and dynamic style data on the same vnode
function normalizeStyleData(data){var style=normalizeStyleBinding(data.style);// static style is pre-processed into an object during compilation
// and is always a fresh object, so it's safe to merge into it
return data.staticStyle?extend(data.staticStyle,style):style;}// normalize possible array / string values into Object
function normalizeStyleBinding(bindingStyle){if(Array.isArray(bindingStyle)){return toObject(bindingStyle);}if(typeof bindingStyle==='string'){return parseStyleText(bindingStyle);}return bindingStyle;}/**
   * parent component style should be after child's
   * so that parent component's style could override it
   */function getStyle(vnode,checkChild){var res={};var styleData;if(checkChild){var childNode=vnode;while(childNode.componentInstance){childNode=childNode.componentInstance._vnode;if(childNode&&childNode.data&&(styleData=normalizeStyleData(childNode.data))){extend(res,styleData);}}}if(styleData=normalizeStyleData(vnode.data)){extend(res,styleData);}var parentNode=vnode;while(parentNode=parentNode.parent){if(parentNode.data&&(styleData=normalizeStyleData(parentNode.data))){extend(res,styleData);}}return res;}/*  */var cssVarRE=/^--/;var importantRE=/\s*!important$/;var setProp=function setProp(el,name,val){/* istanbul ignore if */if(cssVarRE.test(name)){el.style.setProperty(name,val);}else if(importantRE.test(val)){el.style.setProperty(hyphenate(name),val.replace(importantRE,''),'important');}else{var normalizedName=normalize(name);if(Array.isArray(val)){// Support values array created by autoprefixer, e.g.
// {display: ["-webkit-box", "-ms-flexbox", "flex"]}
// Set them one by one, and the browser will only set those it can recognize
for(var i=0,len=val.length;i<len;i++){el.style[normalizedName]=val[i];}}else{el.style[normalizedName]=val;}}};var vendorNames=['Webkit','Moz','ms'];var emptyStyle;var normalize=cached(function(prop){emptyStyle=emptyStyle||document.createElement('div').style;prop=camelize(prop);if(prop!=='filter'&&prop in emptyStyle){return prop;}var capName=prop.charAt(0).toUpperCase()+prop.slice(1);for(var i=0;i<vendorNames.length;i++){var name=vendorNames[i]+capName;if(name in emptyStyle){return name;}}});function updateStyle(oldVnode,vnode){var data=vnode.data;var oldData=oldVnode.data;if(isUndef(data.staticStyle)&&isUndef(data.style)&&isUndef(oldData.staticStyle)&&isUndef(oldData.style)){return;}var cur,name;var el=vnode.elm;var oldStaticStyle=oldData.staticStyle;var oldStyleBinding=oldData.normalizedStyle||oldData.style||{};// if static style exists, stylebinding already merged into it when doing normalizeStyleData
var oldStyle=oldStaticStyle||oldStyleBinding;var style=normalizeStyleBinding(vnode.data.style)||{};// store normalized style under a different key for next diff
// make sure to clone it if it's reactive, since the user likely wants
// to mutate it.
vnode.data.normalizedStyle=isDef(style.__ob__)?extend({},style):style;var newStyle=getStyle(vnode,true);for(name in oldStyle){if(isUndef(newStyle[name])){setProp(el,name,'');}}for(name in newStyle){cur=newStyle[name];if(cur!==oldStyle[name]){// ie9 setting to null has no effect, must use empty string
setProp(el,name,cur==null?'':cur);}}}var style={create:updateStyle,update:updateStyle};/*  */var whitespaceRE=/\s+/;/**
   * Add class with compatibility for SVG since classList is not supported on
   * SVG elements in IE
   */function addClass(el,cls){/* istanbul ignore if */if(!cls||!(cls=cls.trim())){return;}/* istanbul ignore else */if(el.classList){if(cls.indexOf(' ')>-1){cls.split(whitespaceRE).forEach(function(c){return el.classList.add(c);});}else{el.classList.add(cls);}}else{var cur=" "+(el.getAttribute('class')||'')+" ";if(cur.indexOf(' '+cls+' ')<0){el.setAttribute('class',(cur+cls).trim());}}}/**
   * Remove class with compatibility for SVG since classList is not supported on
   * SVG elements in IE
   */function removeClass(el,cls){/* istanbul ignore if */if(!cls||!(cls=cls.trim())){return;}/* istanbul ignore else */if(el.classList){if(cls.indexOf(' ')>-1){cls.split(whitespaceRE).forEach(function(c){return el.classList.remove(c);});}else{el.classList.remove(cls);}if(!el.classList.length){el.removeAttribute('class');}}else{var cur=" "+(el.getAttribute('class')||'')+" ";var tar=' '+cls+' ';while(cur.indexOf(tar)>=0){cur=cur.replace(tar,' ');}cur=cur.trim();if(cur){el.setAttribute('class',cur);}else{el.removeAttribute('class');}}}/*  */function resolveTransition(def$$1){if(!def$$1){return;}/* istanbul ignore else */if(_typeof2(def$$1)==='object'){var res={};if(def$$1.css!==false){extend(res,autoCssTransition(def$$1.name||'v'));}extend(res,def$$1);return res;}else if(typeof def$$1==='string'){return autoCssTransition(def$$1);}}var autoCssTransition=cached(function(name){return{enterClass:name+"-enter",enterToClass:name+"-enter-to",enterActiveClass:name+"-enter-active",leaveClass:name+"-leave",leaveToClass:name+"-leave-to",leaveActiveClass:name+"-leave-active"};});var hasTransition=inBrowser&&!isIE9;var TRANSITION='transition';var ANIMATION='animation';// Transition property/event sniffing
var transitionProp='transition';var transitionEndEvent='transitionend';var animationProp='animation';var animationEndEvent='animationend';if(hasTransition){/* istanbul ignore if */if(window.ontransitionend===undefined&&window.onwebkittransitionend!==undefined){transitionProp='WebkitTransition';transitionEndEvent='webkitTransitionEnd';}if(window.onanimationend===undefined&&window.onwebkitanimationend!==undefined){animationProp='WebkitAnimation';animationEndEvent='webkitAnimationEnd';}}// binding to window is necessary to make hot reload work in IE in strict mode
var raf=inBrowser?window.requestAnimationFrame?window.requestAnimationFrame.bind(window):setTimeout:/* istanbul ignore next */function(fn){return fn();};function nextFrame(fn){raf(function(){raf(fn);});}function addTransitionClass(el,cls){var transitionClasses=el._transitionClasses||(el._transitionClasses=[]);if(transitionClasses.indexOf(cls)<0){transitionClasses.push(cls);addClass(el,cls);}}function removeTransitionClass(el,cls){if(el._transitionClasses){remove(el._transitionClasses,cls);}removeClass(el,cls);}function whenTransitionEnds(el,expectedType,cb){var ref=getTransitionInfo(el,expectedType);var type=ref.type;var timeout=ref.timeout;var propCount=ref.propCount;if(!type){return cb();}var event=type===TRANSITION?transitionEndEvent:animationEndEvent;var ended=0;var end=function end(){el.removeEventListener(event,onEnd);cb();};var onEnd=function onEnd(e){if(e.target===el){if(++ended>=propCount){end();}}};setTimeout(function(){if(ended<propCount){end();}},timeout+1);el.addEventListener(event,onEnd);}var transformRE=/\b(transform|all)(,|$)/;function getTransitionInfo(el,expectedType){var styles=window.getComputedStyle(el);// JSDOM may return undefined for transition properties
var transitionDelays=(styles[transitionProp+'Delay']||'').split(', ');var transitionDurations=(styles[transitionProp+'Duration']||'').split(', ');var transitionTimeout=getTimeout(transitionDelays,transitionDurations);var animationDelays=(styles[animationProp+'Delay']||'').split(', ');var animationDurations=(styles[animationProp+'Duration']||'').split(', ');var animationTimeout=getTimeout(animationDelays,animationDurations);var type;var timeout=0;var propCount=0;/* istanbul ignore if */if(expectedType===TRANSITION){if(transitionTimeout>0){type=TRANSITION;timeout=transitionTimeout;propCount=transitionDurations.length;}}else if(expectedType===ANIMATION){if(animationTimeout>0){type=ANIMATION;timeout=animationTimeout;propCount=animationDurations.length;}}else{timeout=Math.max(transitionTimeout,animationTimeout);type=timeout>0?transitionTimeout>animationTimeout?TRANSITION:ANIMATION:null;propCount=type?type===TRANSITION?transitionDurations.length:animationDurations.length:0;}var hasTransform=type===TRANSITION&&transformRE.test(styles[transitionProp+'Property']);return{type:type,timeout:timeout,propCount:propCount,hasTransform:hasTransform};}function getTimeout(delays,durations){/* istanbul ignore next */while(delays.length<durations.length){delays=delays.concat(delays);}return Math.max.apply(null,durations.map(function(d,i){return toMs(d)+toMs(delays[i]);}));}// Old versions of Chromium (below 61.0.3163.100) formats floating pointer numbers
// in a locale-dependent way, using a comma instead of a dot.
// If comma is not replaced with a dot, the input will be rounded down (i.e. acting
// as a floor function) causing unexpected behaviors
function toMs(s){return Number(s.slice(0,-1).replace(',','.'))*1000;}/*  */function enter(vnode,toggleDisplay){var el=vnode.elm;// call leave callback now
if(isDef(el._leaveCb)){el._leaveCb.cancelled=true;el._leaveCb();}var data=resolveTransition(vnode.data.transition);if(isUndef(data)){return;}/* istanbul ignore if */if(isDef(el._enterCb)||el.nodeType!==1){return;}var css=data.css;var type=data.type;var enterClass=data.enterClass;var enterToClass=data.enterToClass;var enterActiveClass=data.enterActiveClass;var appearClass=data.appearClass;var appearToClass=data.appearToClass;var appearActiveClass=data.appearActiveClass;var beforeEnter=data.beforeEnter;var enter=data.enter;var afterEnter=data.afterEnter;var enterCancelled=data.enterCancelled;var beforeAppear=data.beforeAppear;var appear=data.appear;var afterAppear=data.afterAppear;var appearCancelled=data.appearCancelled;var duration=data.duration;// activeInstance will always be the <transition> component managing this
// transition. One edge case to check is when the <transition> is placed
// as the root node of a child component. In that case we need to check
// <transition>'s parent for appear check.
var context=activeInstance;var transitionNode=activeInstance.$vnode;while(transitionNode&&transitionNode.parent){context=transitionNode.context;transitionNode=transitionNode.parent;}var isAppear=!context._isMounted||!vnode.isRootInsert;if(isAppear&&!appear&&appear!==''){return;}var startClass=isAppear&&appearClass?appearClass:enterClass;var activeClass=isAppear&&appearActiveClass?appearActiveClass:enterActiveClass;var toClass=isAppear&&appearToClass?appearToClass:enterToClass;var beforeEnterHook=isAppear?beforeAppear||beforeEnter:beforeEnter;var enterHook=isAppear?typeof appear==='function'?appear:enter:enter;var afterEnterHook=isAppear?afterAppear||afterEnter:afterEnter;var enterCancelledHook=isAppear?appearCancelled||enterCancelled:enterCancelled;var explicitEnterDuration=toNumber(isObject(duration)?duration.enter:duration);var expectsCSS=css!==false&&!isIE9;var userWantsControl=getHookArgumentsLength(enterHook);var cb=el._enterCb=once(function(){if(expectsCSS){removeTransitionClass(el,toClass);removeTransitionClass(el,activeClass);}if(cb.cancelled){if(expectsCSS){removeTransitionClass(el,startClass);}enterCancelledHook&&enterCancelledHook(el);}else{afterEnterHook&&afterEnterHook(el);}el._enterCb=null;});if(!vnode.data.show){// remove pending leave element on enter by injecting an insert hook
mergeVNodeHook(vnode,'insert',function(){var parent=el.parentNode;var pendingNode=parent&&parent._pending&&parent._pending[vnode.key];if(pendingNode&&pendingNode.tag===vnode.tag&&pendingNode.elm._leaveCb){pendingNode.elm._leaveCb();}enterHook&&enterHook(el,cb);});}// start enter transition
beforeEnterHook&&beforeEnterHook(el);if(expectsCSS){addTransitionClass(el,startClass);addTransitionClass(el,activeClass);nextFrame(function(){removeTransitionClass(el,startClass);if(!cb.cancelled){addTransitionClass(el,toClass);if(!userWantsControl){if(isValidDuration(explicitEnterDuration)){setTimeout(cb,explicitEnterDuration);}else{whenTransitionEnds(el,type,cb);}}}});}if(vnode.data.show){toggleDisplay&&toggleDisplay();enterHook&&enterHook(el,cb);}if(!expectsCSS&&!userWantsControl){cb();}}function leave(vnode,rm){var el=vnode.elm;// call enter callback now
if(isDef(el._enterCb)){el._enterCb.cancelled=true;el._enterCb();}var data=resolveTransition(vnode.data.transition);if(isUndef(data)||el.nodeType!==1){return rm();}/* istanbul ignore if */if(isDef(el._leaveCb)){return;}var css=data.css;var type=data.type;var leaveClass=data.leaveClass;var leaveToClass=data.leaveToClass;var leaveActiveClass=data.leaveActiveClass;var beforeLeave=data.beforeLeave;var leave=data.leave;var afterLeave=data.afterLeave;var leaveCancelled=data.leaveCancelled;var delayLeave=data.delayLeave;var duration=data.duration;var expectsCSS=css!==false&&!isIE9;var userWantsControl=getHookArgumentsLength(leave);var explicitLeaveDuration=toNumber(isObject(duration)?duration.leave:duration);var cb=el._leaveCb=once(function(){if(el.parentNode&&el.parentNode._pending){el.parentNode._pending[vnode.key]=null;}if(expectsCSS){removeTransitionClass(el,leaveToClass);removeTransitionClass(el,leaveActiveClass);}if(cb.cancelled){if(expectsCSS){removeTransitionClass(el,leaveClass);}leaveCancelled&&leaveCancelled(el);}else{rm();afterLeave&&afterLeave(el);}el._leaveCb=null;});if(delayLeave){delayLeave(performLeave);}else{performLeave();}function performLeave(){// the delayed leave may have already been cancelled
if(cb.cancelled){return;}// record leaving element
if(!vnode.data.show&&el.parentNode){(el.parentNode._pending||(el.parentNode._pending={}))[vnode.key]=vnode;}beforeLeave&&beforeLeave(el);if(expectsCSS){addTransitionClass(el,leaveClass);addTransitionClass(el,leaveActiveClass);nextFrame(function(){removeTransitionClass(el,leaveClass);if(!cb.cancelled){addTransitionClass(el,leaveToClass);if(!userWantsControl){if(isValidDuration(explicitLeaveDuration)){setTimeout(cb,explicitLeaveDuration);}else{whenTransitionEnds(el,type,cb);}}}});}leave&&leave(el,cb);if(!expectsCSS&&!userWantsControl){cb();}}}function isValidDuration(val){return typeof val==='number'&&!isNaN(val);}/**
   * Normalize a transition hook's argument length. The hook may be:
   * - a merged hook (invoker) with the original in .fns
   * - a wrapped component method (check ._length)
   * - a plain function (.length)
   */function getHookArgumentsLength(fn){if(isUndef(fn)){return false;}var invokerFns=fn.fns;if(isDef(invokerFns)){// invoker
return getHookArgumentsLength(Array.isArray(invokerFns)?invokerFns[0]:invokerFns);}else{return(fn._length||fn.length)>1;}}function _enter(_,vnode){if(vnode.data.show!==true){enter(vnode);}}var transition=inBrowser?{create:_enter,activate:_enter,remove:function remove$$1(vnode,rm){/* istanbul ignore else */if(vnode.data.show!==true){leave(vnode,rm);}else{rm();}}}:{};var platformModules=[attrs,klass,events,domProps,style,transition];/*  */ // the directive module should be applied last, after all
// built-in modules have been applied.
var modules=platformModules.concat(baseModules);var patch=createPatchFunction({nodeOps:nodeOps,modules:modules});/**
   * Not type checking this file because flow doesn't like attaching
   * properties to Elements.
   */ /* istanbul ignore if */if(isIE9){// http://www.matts411.com/post/internet-explorer-9-oninput/
document.addEventListener('selectionchange',function(){var el=document.activeElement;if(el&&el.vmodel){trigger(el,'input');}});}var directive={inserted:function inserted(el,binding,vnode,oldVnode){if(vnode.tag==='select'){// #6903
if(oldVnode.elm&&!oldVnode.elm._vOptions){mergeVNodeHook(vnode,'postpatch',function(){directive.componentUpdated(el,binding,vnode);});}else{setSelected(el,binding,vnode.context);}el._vOptions=[].map.call(el.options,getValue);}else if(vnode.tag==='textarea'||isTextInputType(el.type)){el._vModifiers=binding.modifiers;if(!binding.modifiers.lazy){el.addEventListener('compositionstart',onCompositionStart);el.addEventListener('compositionend',onCompositionEnd);// Safari < 10.2 & UIWebView doesn't fire compositionend when
// switching focus before confirming composition choice
// this also fixes the issue where some browsers e.g. iOS Chrome
// fires "change" instead of "input" on autocomplete.
el.addEventListener('change',onCompositionEnd);/* istanbul ignore if */if(isIE9){el.vmodel=true;}}}},componentUpdated:function componentUpdated(el,binding,vnode){if(vnode.tag==='select'){setSelected(el,binding,vnode.context);// in case the options rendered by v-for have changed,
// it's possible that the value is out-of-sync with the rendered options.
// detect such cases and filter out values that no longer has a matching
// option in the DOM.
var prevOptions=el._vOptions;var curOptions=el._vOptions=[].map.call(el.options,getValue);if(curOptions.some(function(o,i){return!looseEqual(o,prevOptions[i]);})){// trigger change event if
// no matching option found for at least one value
var needReset=el.multiple?binding.value.some(function(v){return hasNoMatchingOption(v,curOptions);}):binding.value!==binding.oldValue&&hasNoMatchingOption(binding.value,curOptions);if(needReset){trigger(el,'change');}}}}};function setSelected(el,binding,vm){actuallySetSelected(el,binding);/* istanbul ignore if */if(isIE||isEdge){setTimeout(function(){actuallySetSelected(el,binding);},0);}}function actuallySetSelected(el,binding,vm){var value=binding.value;var isMultiple=el.multiple;if(isMultiple&&!Array.isArray(value)){return;}var selected,option;for(var i=0,l=el.options.length;i<l;i++){option=el.options[i];if(isMultiple){selected=looseIndexOf(value,getValue(option))>-1;if(option.selected!==selected){option.selected=selected;}}else{if(looseEqual(getValue(option),value)){if(el.selectedIndex!==i){el.selectedIndex=i;}return;}}}if(!isMultiple){el.selectedIndex=-1;}}function hasNoMatchingOption(value,options){return options.every(function(o){return!looseEqual(o,value);});}function getValue(option){return'_value'in option?option._value:option.value;}function onCompositionStart(e){e.target.composing=true;}function onCompositionEnd(e){// prevent triggering an input event for no reason
if(!e.target.composing){return;}e.target.composing=false;trigger(e.target,'input');}function trigger(el,type){var e=document.createEvent('HTMLEvents');e.initEvent(type,true,true);el.dispatchEvent(e);}/*  */ // recursively search for possible transition defined inside the component root
function locateNode(vnode){return vnode.componentInstance&&(!vnode.data||!vnode.data.transition)?locateNode(vnode.componentInstance._vnode):vnode;}var show={bind:function bind(el,ref,vnode){var value=ref.value;vnode=locateNode(vnode);var transition$$1=vnode.data&&vnode.data.transition;var originalDisplay=el.__vOriginalDisplay=el.style.display==='none'?'':el.style.display;if(value&&transition$$1){vnode.data.show=true;enter(vnode,function(){el.style.display=originalDisplay;});}else{el.style.display=value?originalDisplay:'none';}},update:function update(el,ref,vnode){var value=ref.value;var oldValue=ref.oldValue;/* istanbul ignore if */if(!value===!oldValue){return;}vnode=locateNode(vnode);var transition$$1=vnode.data&&vnode.data.transition;if(transition$$1){vnode.data.show=true;if(value){enter(vnode,function(){el.style.display=el.__vOriginalDisplay;});}else{leave(vnode,function(){el.style.display='none';});}}else{el.style.display=value?el.__vOriginalDisplay:'none';}},unbind:function unbind(el,binding,vnode,oldVnode,isDestroy){if(!isDestroy){el.style.display=el.__vOriginalDisplay;}}};var platformDirectives={model:directive,show:show};/*  */var transitionProps={name:String,appear:Boolean,css:Boolean,mode:String,type:String,enterClass:String,leaveClass:String,enterToClass:String,leaveToClass:String,enterActiveClass:String,leaveActiveClass:String,appearClass:String,appearActiveClass:String,appearToClass:String,duration:[Number,String,Object]};// in case the child is also an abstract component, e.g. <keep-alive>
// we want to recursively retrieve the real component to be rendered
function getRealChild(vnode){var compOptions=vnode&&vnode.componentOptions;if(compOptions&&compOptions.Ctor.options["abstract"]){return getRealChild(getFirstComponentChild(compOptions.children));}else{return vnode;}}function extractTransitionData(comp){var data={};var options=comp.$options;// props
for(var key in options.propsData){data[key]=comp[key];}// events.
// extract listeners and pass them directly to the transition methods
var listeners=options._parentListeners;for(var key$1 in listeners){data[camelize(key$1)]=listeners[key$1];}return data;}function placeholder(h,rawChild){if(/\d-keep-alive$/.test(rawChild.tag)){return h('keep-alive',{props:rawChild.componentOptions.propsData});}}function hasParentTransition(vnode){while(vnode=vnode.parent){if(vnode.data.transition){return true;}}}function isSameChild(child,oldChild){return oldChild.key===child.key&&oldChild.tag===child.tag;}var isNotTextNode=function isNotTextNode(c){return c.tag||isAsyncPlaceholder(c);};var isVShowDirective=function isVShowDirective(d){return d.name==='show';};var Transition={name:'transition',props:transitionProps,"abstract":true,render:function render(h){var this$1=this;var children=this.$slots["default"];if(!children){return;}// filter out text nodes (possible whitespaces)
children=children.filter(isNotTextNode);/* istanbul ignore if */if(!children.length){return;}var mode=this.mode;var rawChild=children[0];// if this is a component root node and the component's
// parent container node also has transition, skip.
if(hasParentTransition(this.$vnode)){return rawChild;}// apply transition data to child
// use getRealChild() to ignore abstract components e.g. keep-alive
var child=getRealChild(rawChild);/* istanbul ignore if */if(!child){return rawChild;}if(this._leaving){return placeholder(h,rawChild);}// ensure a key that is unique to the vnode type and to this transition
// component instance. This key will be used to remove pending leaving nodes
// during entering.
var id="__transition-"+this._uid+"-";child.key=child.key==null?child.isComment?id+'comment':id+child.tag:isPrimitive(child.key)?String(child.key).indexOf(id)===0?child.key:id+child.key:child.key;var data=(child.data||(child.data={})).transition=extractTransitionData(this);var oldRawChild=this._vnode;var oldChild=getRealChild(oldRawChild);// mark v-show
// so that the transition module can hand over the control to the directive
if(child.data.directives&&child.data.directives.some(isVShowDirective)){child.data.show=true;}if(oldChild&&oldChild.data&&!isSameChild(child,oldChild)&&!isAsyncPlaceholder(oldChild)&&// #6687 component root is a comment node
!(oldChild.componentInstance&&oldChild.componentInstance._vnode.isComment)){// replace old child transition data with fresh one
// important for dynamic transitions!
var oldData=oldChild.data.transition=extend({},data);// handle transition mode
if(mode==='out-in'){// return placeholder node and queue update when leave finishes
this._leaving=true;mergeVNodeHook(oldData,'afterLeave',function(){this$1._leaving=false;this$1.$forceUpdate();});return placeholder(h,rawChild);}else if(mode==='in-out'){if(isAsyncPlaceholder(child)){return oldRawChild;}var delayedLeave;var performLeave=function performLeave(){delayedLeave();};mergeVNodeHook(data,'afterEnter',performLeave);mergeVNodeHook(data,'enterCancelled',performLeave);mergeVNodeHook(oldData,'delayLeave',function(leave){delayedLeave=leave;});}}return rawChild;}};/*  */var props=extend({tag:String,moveClass:String},transitionProps);delete props.mode;var TransitionGroup={props:props,beforeMount:function beforeMount(){var this$1=this;var update=this._update;this._update=function(vnode,hydrating){var restoreActiveInstance=setActiveInstance(this$1);// force removing pass
this$1.__patch__(this$1._vnode,this$1.kept,false,// hydrating
true// removeOnly (!important, avoids unnecessary moves)
);this$1._vnode=this$1.kept;restoreActiveInstance();update.call(this$1,vnode,hydrating);};},render:function render(h){var tag=this.tag||this.$vnode.data.tag||'span';var map=Object.create(null);var prevChildren=this.prevChildren=this.children;var rawChildren=this.$slots["default"]||[];var children=this.children=[];var transitionData=extractTransitionData(this);for(var i=0;i<rawChildren.length;i++){var c=rawChildren[i];if(c.tag){if(c.key!=null&&String(c.key).indexOf('__vlist')!==0){children.push(c);map[c.key]=c;(c.data||(c.data={})).transition=transitionData;}}}if(prevChildren){var kept=[];var removed=[];for(var i$1=0;i$1<prevChildren.length;i$1++){var c$1=prevChildren[i$1];c$1.data.transition=transitionData;c$1.data.pos=c$1.elm.getBoundingClientRect();if(map[c$1.key]){kept.push(c$1);}else{removed.push(c$1);}}this.kept=h(tag,null,kept);this.removed=removed;}return h(tag,null,children);},updated:function updated(){var children=this.prevChildren;var moveClass=this.moveClass||(this.name||'v')+'-move';if(!children.length||!this.hasMove(children[0].elm,moveClass)){return;}// we divide the work into three loops to avoid mixing DOM reads and writes
// in each iteration - which helps prevent layout thrashing.
children.forEach(callPendingCbs);children.forEach(recordPosition);children.forEach(applyTranslation);// force reflow to put everything in position
// assign to this to avoid being removed in tree-shaking
// $flow-disable-line
this._reflow=document.body.offsetHeight;children.forEach(function(c){if(c.data.moved){var el=c.elm;var s=el.style;addTransitionClass(el,moveClass);s.transform=s.WebkitTransform=s.transitionDuration='';el.addEventListener(transitionEndEvent,el._moveCb=function cb(e){if(e&&e.target!==el){return;}if(!e||/transform$/.test(e.propertyName)){el.removeEventListener(transitionEndEvent,cb);el._moveCb=null;removeTransitionClass(el,moveClass);}});}});},methods:{hasMove:function hasMove(el,moveClass){/* istanbul ignore if */if(!hasTransition){return false;}/* istanbul ignore if */if(this._hasMove){return this._hasMove;}// Detect whether an element with the move class applied has
// CSS transitions. Since the element may be inside an entering
// transition at this very moment, we make a clone of it and remove
// all other transition classes applied to ensure only the move class
// is applied.
var clone=el.cloneNode();if(el._transitionClasses){el._transitionClasses.forEach(function(cls){removeClass(clone,cls);});}addClass(clone,moveClass);clone.style.display='none';this.$el.appendChild(clone);var info=getTransitionInfo(clone);this.$el.removeChild(clone);return this._hasMove=info.hasTransform;}}};function callPendingCbs(c){/* istanbul ignore if */if(c.elm._moveCb){c.elm._moveCb();}/* istanbul ignore if */if(c.elm._enterCb){c.elm._enterCb();}}function recordPosition(c){c.data.newPos=c.elm.getBoundingClientRect();}function applyTranslation(c){var oldPos=c.data.pos;var newPos=c.data.newPos;var dx=oldPos.left-newPos.left;var dy=oldPos.top-newPos.top;if(dx||dy){c.data.moved=true;var s=c.elm.style;s.transform=s.WebkitTransform="translate("+dx+"px,"+dy+"px)";s.transitionDuration='0s';}}var platformComponents={Transition:Transition,TransitionGroup:TransitionGroup};/*  */ // install platform specific utils
Vue.config.mustUseProp=mustUseProp;Vue.config.isReservedTag=isReservedTag;Vue.config.isReservedAttr=isReservedAttr;Vue.config.getTagNamespace=getTagNamespace;Vue.config.isUnknownElement=isUnknownElement;// install platform runtime directives & components
extend(Vue.options.directives,platformDirectives);extend(Vue.options.components,platformComponents);// install platform patch function
Vue.prototype.__patch__=inBrowser?patch:noop;// public mount method
Vue.prototype.$mount=function(el,hydrating){el=el&&inBrowser?query(el):undefined;return mountComponent(this,el,hydrating);};// devtools global hook
/* istanbul ignore next */if(inBrowser){setTimeout(function(){if(config.devtools){if(devtools){devtools.emit('init',Vue);}}},0);}var _typeof=typeof Symbol==="function"&&_typeof2(Symbol.iterator)==="symbol"?function(obj){return _typeof2(obj);}:function(obj){return obj&&typeof Symbol==="function"&&obj.constructor===Symbol&&obj!==Symbol.prototype?"symbol":_typeof2(obj);};// -----------------------------------------------------------------------------
// Init
var initializeGlobalNamespace=function initializeGlobalNamespace(){var namespace=void 0;if((typeof window==='undefined'?'undefined':_typeof(window))==='object'){namespace=window.filestackInternals;if(!namespace){namespace={};window.filestackInternals=namespace;}if(!namespace.loader){namespace.loader={modules:{}};}}return namespace;};var filestackInternals=initializeGlobalNamespace();// -----------------------------------------------------------------------------
// Modules loading
// All modules share global "register", so different instances of loader can
// communicate which modules were already loaded and which not.
var modules$1=filestackInternals&&filestackInternals.loader.modules;var loadModule=function loadModule(url,moduleId){var moduleDefinition=modules$1[url];if(!moduleDefinition){modules$1[url]={};moduleDefinition=modules$1[url];}if(moduleDefinition.instance){return Promise.resolve(moduleDefinition.instance);}if(moduleDefinition.promise){return moduleDefinition.promise;}var promise=new Promise(function(resolve,reject){var embedScript=function embedScript(){moduleDefinition.resolvePromise=resolve;var script=document.createElement('script');script.src=url;script.onerror=reject;if(moduleId)script.id=moduleId;document.body.appendChild(script);};var checkIfDomReady=function checkIfDomReady(){if(document.readyState==='complete'){embedScript();}else{setTimeout(checkIfDomReady,50);}};checkIfDomReady();});moduleDefinition.promise=promise;return promise;};var registerReadyModule=function registerReadyModule(instance,moduleId){var thisScript=void 0;if(moduleId&&document.getElementById(moduleId)){thisScript=document.getElementById(moduleId);}else{var scriptTags=document.getElementsByTagName('script');thisScript=scriptTags[scriptTags.length-1];}var url=thisScript.getAttribute('src');var moduleDefinition=modules$1[url];if(moduleDefinition&&moduleDefinition.resolvePromise){moduleDefinition.instance=instance;moduleDefinition.resolvePromise(instance);delete moduleDefinition.promise;delete moduleDefinition.resolvePromise;}};// -----------------------------------------------------------------------------
// CSS loading
var loadCss=function loadCss(url){var alreadyAddedThisTag=document.querySelector('link[href="'+url+'"]');if(alreadyAddedThisTag!==null){return Promise.resolve();}return new Promise(function(resolve){var head=document.getElementsByTagName('head')[0];var link=document.createElement('link');var loaded=function loaded(){resolve();link.removeEventListener('load',loaded);};link.rel='stylesheet';link.href=url;link.addEventListener('load',loaded);head.appendChild(link);});};var knownModuleIds={picker:'__filestack-picker-module'};// Logger can be used and required from many places.
// This is global on / off switch for it, which all
// created logger contexts respect.
var onOff={init:function init(){window.filestackInternals.logger.working=false;},isWorking:function isWorking(){return window.filestackInternals.logger.working;},on:function on(){window.filestackInternals.logger.working=true;},off:function off(){window.filestackInternals.logger.working=false;}};var _typeof$1=typeof Symbol==="function"&&_typeof2(Symbol.iterator)==="symbol"?function(obj){return _typeof2(obj);}:function(obj){return obj&&typeof Symbol==="function"&&obj.constructor===Symbol&&obj!==Symbol.prototype?"symbol":_typeof2(obj);};var toConsumableArray=function toConsumableArray(arr){if(Array.isArray(arr)){for(var i=0,arr2=Array(arr.length);i<arr.length;i++){arr2[i]=arr[i];}return arr2;}else{return Array.from(arr);}};/* eslint no-console:0 */var context=function context(contextName,onOff){var api=function log(){for(var _len=arguments.length,stuff=Array(_len),_key=0;_key<_len;_key++){stuff[_key]=arguments[_key];}var convertedToStrings=[].concat(stuff).map(function(thing){if((typeof thing==='undefined'?'undefined':_typeof$1(thing))==='object'){return JSON.stringify(thing,function(key,value){if(typeof value==='function'){// If any function named json is found then call that function to get the json object.
if(key==='json'){try{return value();}catch(err){// Throws? No worries. Just go on and return string.
}}return'[Function]';}if(value instanceof File){return'[File name: '+value.name+', mimetype: '+value.type+', size: '+value.size+']';}return value;},2);}return thing;});if(onOff.isWorking()){var _console;(_console=console).log.apply(_console,['['+contextName+']'].concat(toConsumableArray(convertedToStrings)));}};api.context=function(subContextName){return context(contextName+']['+subContextName,onOff);};api.on=onOff.on;api.off=onOff.off;return api;};var logger=context('filestack',onOff);var initializeGlobalNamespace$1=function initializeGlobalNamespace(){var namespace=void 0;if((typeof window==='undefined'?'undefined':_typeof$1(window))==='object'){namespace=window.filestackInternals;if(!namespace){namespace={};window.filestackInternals=namespace;}if(!namespace.logger){namespace.logger=logger;onOff.init();}}return namespace;};initializeGlobalNamespace$1();var commonjsGlobal=typeof globalThis!=='undefined'?globalThis:typeof window!=='undefined'?window:typeof global!=='undefined'?global:typeof self!=='undefined'?self:{};function commonjsRequire(){throw new Error('Dynamic requires are not currently supported by rollup-plugin-commonjs');}function unwrapExports(x){return x&&x.__esModule&&Object.prototype.hasOwnProperty.call(x,'default')?x['default']:x;}function createCommonjsModule(fn,module){return module={exports:{}},fn(module,module.exports),module.exports;}var vueSessionstorage_min=createCommonjsModule(function(module,exports){!function(t,e){module.exports=e();}(commonjsGlobal,function(){return function(t){function e(i){if(s[i])return s[i].exports;var n=s[i]={exports:{},id:i,loaded:!1};return t[i].call(n.exports,n,n.exports,e),n.loaded=!0,n.exports;}var s={};return e.m=t,e.c=s,e.p="",e(0);}([function(t,e,s){function i(t){return t&&t.__esModule?t:{"default":t};}var n=s(2),o=i(n),r=s(1),u=i(r);window.sessionStorage||(window.sessionStorage=u["default"]);var a={install:function install(t,e){t.prototype.$session=new o["default"]();}};t.exports=a;},function(t,e){function s(){this.data={},this.setItem=function(t,e){this.data[t]=e;},this.getItem=function(t){return this.data[t];};}t.exports=s;},function(t,e){function s(){this.key=null,this.__getRandomString=function(){for(var t=arguments.length>0&&void 0!==arguments[0]?arguments[0]:10,e="";t--;){e+=String.fromCharCode(48+~~(42*Math.random()));}return e;},this.__getKey=function(){var t=window.sessionStorage.getItem("sessionKey");t||(t=this.__getRandomString(),window.sessionStorage.setItem("sessionKey",t)),this.key=t;},this.__get=function(){this.key||this.__getKey();var t=JSON.parse(window.sessionStorage.getItem(this.key));return t||{};},this.get=function(t){var e=this.__get();return e[t];},this.__set=function(t){this.key||this.__getKey(),window.sessionStorage.setItem(this.key,JSON.stringify(t));},this.set=function(t,e){var s=this.__get();s[t]=e,this.__set(s);},this.exists=function(t){var e=this.__get();return t in e;},this.remove=function(t){var e=this.__get();delete e[t],this.__set(e);},this.clear=function(){this.__set({});};}t.exports=s;}]);});});var VueSessionStorage=unwrapExports(vueSessionstorage_min);var vueSessionstorage_min_1=vueSessionstorage_min.VueSessionStorage;/**
   * lodash 3.0.0 (Custom Build) <https://lodash.com/>
   * Build: `lodash modern modularize exports="npm" -o ./`
   * Copyright 2012-2015 The Dojo Foundation <http://dojofoundation.org/>
   * Based on Underscore.js 1.7.0 <http://underscorejs.org/LICENSE>
   * Copyright 2009-2015 Jeremy Ashkenas, DocumentCloud and Investigative Reporters & Editors
   * Available under MIT license <https://lodash.com/license>
   */ /** Used to match template delimiters. */var reInterpolate=/<%=([\s\S]+?)%>/g;var lodash__reinterpolate=reInterpolate;/**
   * Lodash (Custom Build) <https://lodash.com/>
   * Build: `lodash modularize exports="npm" -o ./`
   * Copyright OpenJS Foundation and other contributors <https://openjsf.org/>
   * Released under MIT license <https://lodash.com/license>
   * Based on Underscore.js 1.8.3 <http://underscorejs.org/LICENSE>
   * Copyright Jeremy Ashkenas, DocumentCloud and Investigative Reporters & Editors
   */ /** Used as references for various `Number` constants. */var INFINITY=1/0;/** `Object#toString` result references. */var nullTag='[object Null]',symbolTag='[object Symbol]',undefinedTag='[object Undefined]';/** Used to match HTML entities and HTML characters. */var reUnescapedHtml=/[&<>"']/g,reHasUnescapedHtml=RegExp(reUnescapedHtml.source);/** Used to match template delimiters. */var reEscape=/<%-([\s\S]+?)%>/g,reEvaluate=/<%([\s\S]+?)%>/g;/** Used to map characters to HTML entities. */var htmlEscapes={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};/** Detect free variable `global` from Node.js. */var freeGlobal=_typeof2(commonjsGlobal)=='object'&&commonjsGlobal&&commonjsGlobal.Object===Object&&commonjsGlobal;/** Detect free variable `self`. */var freeSelf=(typeof self==="undefined"?"undefined":_typeof2(self))=='object'&&self&&self.Object===Object&&self;/** Used as a reference to the global object. */var root=freeGlobal||freeSelf||Function('return this')();/**
   * A specialized version of `_.map` for arrays without support for iteratee
   * shorthands.
   *
   * @private
   * @param {Array} [array] The array to iterate over.
   * @param {Function} iteratee The function invoked per iteration.
   * @returns {Array} Returns the new mapped array.
   */function arrayMap(array,iteratee){var index=-1,length=array==null?0:array.length,result=Array(length);while(++index<length){result[index]=iteratee(array[index],index,array);}return result;}/**
   * The base implementation of `_.propertyOf` without support for deep paths.
   *
   * @private
   * @param {Object} object The object to query.
   * @returns {Function} Returns the new accessor function.
   */function basePropertyOf(object){return function(key){return object==null?undefined:object[key];};}/**
   * Used by `_.escape` to convert characters to HTML entities.
   *
   * @private
   * @param {string} chr The matched character to escape.
   * @returns {string} Returns the escaped character.
   */var escapeHtmlChar=basePropertyOf(htmlEscapes);/** Used for built-in method references. */var objectProto=Object.prototype;/** Used to check objects for own properties. */var hasOwnProperty$1=objectProto.hasOwnProperty;/**
   * Used to resolve the
   * [`toStringTag`](http://ecma-international.org/ecma-262/7.0/#sec-object.prototype.tostring)
   * of values.
   */var nativeObjectToString=objectProto.toString;/** Built-in value references. */var Symbol$1=root.Symbol,symToStringTag=Symbol$1?Symbol$1.toStringTag:undefined;/** Used to convert symbols to primitives and strings. */var symbolProto=Symbol$1?Symbol$1.prototype:undefined,symbolToString=symbolProto?symbolProto.toString:undefined;/**
   * By default, the template delimiters used by lodash are like those in
   * embedded Ruby (ERB) as well as ES2015 template strings. Change the
   * following template settings to use alternative delimiters.
   *
   * @static
   * @memberOf _
   * @type {Object}
   */var templateSettings={/**
     * Used to detect `data` property values to be HTML-escaped.
     *
     * @memberOf _.templateSettings
     * @type {RegExp}
     */'escape':reEscape,/**
     * Used to detect code to be evaluated.
     *
     * @memberOf _.templateSettings
     * @type {RegExp}
     */'evaluate':reEvaluate,/**
     * Used to detect `data` property values to inject.
     *
     * @memberOf _.templateSettings
     * @type {RegExp}
     */'interpolate':lodash__reinterpolate,/**
     * Used to reference the data object in the template text.
     *
     * @memberOf _.templateSettings
     * @type {string}
     */'variable':'',/**
     * Used to import variables into the compiled template.
     *
     * @memberOf _.templateSettings
     * @type {Object}
     */'imports':{/**
       * A reference to the `lodash` function.
       *
       * @memberOf _.templateSettings.imports
       * @type {Function}
       */'_':{'escape':escape}}};/**
   * The base implementation of `getTag` without fallbacks for buggy environments.
   *
   * @private
   * @param {*} value The value to query.
   * @returns {string} Returns the `toStringTag`.
   */function baseGetTag(value){if(value==null){return value===undefined?undefinedTag:nullTag;}return symToStringTag&&symToStringTag in Object(value)?getRawTag(value):objectToString(value);}/**
   * The base implementation of `_.toString` which doesn't convert nullish
   * values to empty strings.
   *
   * @private
   * @param {*} value The value to process.
   * @returns {string} Returns the string.
   */function baseToString(value){// Exit early for strings to avoid a performance hit in some environments.
if(typeof value=='string'){return value;}if(isArray(value)){// Recursively convert values (susceptible to call stack limits).
return arrayMap(value,baseToString)+'';}if(isSymbol(value)){return symbolToString?symbolToString.call(value):'';}var result=value+'';return result=='0'&&1/value==-INFINITY?'-0':result;}/**
   * A specialized version of `baseGetTag` which ignores `Symbol.toStringTag` values.
   *
   * @private
   * @param {*} value The value to query.
   * @returns {string} Returns the raw `toStringTag`.
   */function getRawTag(value){var isOwn=hasOwnProperty$1.call(value,symToStringTag),tag=value[symToStringTag];try{value[symToStringTag]=undefined;var unmasked=true;}catch(e){}var result=nativeObjectToString.call(value);if(unmasked){if(isOwn){value[symToStringTag]=tag;}else{delete value[symToStringTag];}}return result;}/**
   * Converts `value` to a string using `Object.prototype.toString`.
   *
   * @private
   * @param {*} value The value to convert.
   * @returns {string} Returns the converted string.
   */function objectToString(value){return nativeObjectToString.call(value);}/**
   * Checks if `value` is classified as an `Array` object.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an array, else `false`.
   * @example
   *
   * _.isArray([1, 2, 3]);
   * // => true
   *
   * _.isArray(document.body.children);
   * // => false
   *
   * _.isArray('abc');
   * // => false
   *
   * _.isArray(_.noop);
   * // => false
   */var isArray=Array.isArray;/**
   * Checks if `value` is object-like. A value is object-like if it's not `null`
   * and has a `typeof` result of "object".
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is object-like, else `false`.
   * @example
   *
   * _.isObjectLike({});
   * // => true
   *
   * _.isObjectLike([1, 2, 3]);
   * // => true
   *
   * _.isObjectLike(_.noop);
   * // => false
   *
   * _.isObjectLike(null);
   * // => false
   */function isObjectLike(value){return value!=null&&_typeof2(value)=='object';}/**
   * Checks if `value` is classified as a `Symbol` primitive or object.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a symbol, else `false`.
   * @example
   *
   * _.isSymbol(Symbol.iterator);
   * // => true
   *
   * _.isSymbol('abc');
   * // => false
   */function isSymbol(value){return _typeof2(value)=='symbol'||isObjectLike(value)&&baseGetTag(value)==symbolTag;}/**
   * Converts `value` to a string. An empty string is returned for `null`
   * and `undefined` values. The sign of `-0` is preserved.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to convert.
   * @returns {string} Returns the converted string.
   * @example
   *
   * _.toString(null);
   * // => ''
   *
   * _.toString(-0);
   * // => '-0'
   *
   * _.toString([1, 2, 3]);
   * // => '1,2,3'
   */function toString$1(value){return value==null?'':baseToString(value);}/**
   * Converts the characters "&", "<", ">", '"', and "'" in `string` to their
   * corresponding HTML entities.
   *
   * **Note:** No other characters are escaped. To escape additional
   * characters use a third-party library like [_he_](https://mths.be/he).
   *
   * Though the ">" character is escaped for symmetry, characters like
   * ">" and "/" don't need escaping in HTML and have no special meaning
   * unless they're part of a tag or unquoted attribute value. See
   * [Mathias Bynens's article](https://mathiasbynens.be/notes/ambiguous-ampersands)
   * (under "semi-related fun fact") for more details.
   *
   * When working with HTML you should always
   * [quote attribute values](http://wonko.com/post/html-escaping) to reduce
   * XSS vectors.
   *
   * @static
   * @since 0.1.0
   * @memberOf _
   * @category String
   * @param {string} [string=''] The string to escape.
   * @returns {string} Returns the escaped string.
   * @example
   *
   * _.escape('fred, barney, & pebbles');
   * // => 'fred, barney, &amp; pebbles'
   */function escape(string){string=toString$1(string);return string&&reHasUnescapedHtml.test(string)?string.replace(reUnescapedHtml,escapeHtmlChar):string;}var lodash_templatesettings=templateSettings;var lodash_template=createCommonjsModule(function(module,exports){/**
   * Lodash (Custom Build) <https://lodash.com/>
   * Build: `lodash modularize exports="npm" -o ./`
   * Copyright OpenJS Foundation and other contributors <https://openjsf.org/>
   * Released under MIT license <https://lodash.com/license>
   * Based on Underscore.js 1.8.3 <http://underscorejs.org/LICENSE>
   * Copyright Jeremy Ashkenas, DocumentCloud and Investigative Reporters & Editors
   */ /** Used to detect hot functions by number of calls within a span of milliseconds. */var HOT_COUNT=800,HOT_SPAN=16;/** Used as references for various `Number` constants. */var INFINITY=1/0,MAX_SAFE_INTEGER=9007199254740991;/** `Object#toString` result references. */var argsTag='[object Arguments]',arrayTag='[object Array]',asyncTag='[object AsyncFunction]',boolTag='[object Boolean]',dateTag='[object Date]',domExcTag='[object DOMException]',errorTag='[object Error]',funcTag='[object Function]',genTag='[object GeneratorFunction]',mapTag='[object Map]',numberTag='[object Number]',nullTag='[object Null]',objectTag='[object Object]',proxyTag='[object Proxy]',regexpTag='[object RegExp]',setTag='[object Set]',stringTag='[object String]',symbolTag='[object Symbol]',undefinedTag='[object Undefined]',weakMapTag='[object WeakMap]';var arrayBufferTag='[object ArrayBuffer]',dataViewTag='[object DataView]',float32Tag='[object Float32Array]',float64Tag='[object Float64Array]',int8Tag='[object Int8Array]',int16Tag='[object Int16Array]',int32Tag='[object Int32Array]',uint8Tag='[object Uint8Array]',uint8ClampedTag='[object Uint8ClampedArray]',uint16Tag='[object Uint16Array]',uint32Tag='[object Uint32Array]';/** Used to match empty string literals in compiled template source. */var reEmptyStringLeading=/\b__p \+= '';/g,reEmptyStringMiddle=/\b(__p \+=) '' \+/g,reEmptyStringTrailing=/(__e\(.*?\)|\b__t\)) \+\n'';/g;/**
   * Used to match `RegExp`
   * [syntax characters](http://ecma-international.org/ecma-262/7.0/#sec-patterns).
   */var reRegExpChar=/[\\^$.*+?()[\]{}|]/g;/**
   * Used to match
   * [ES template delimiters](http://ecma-international.org/ecma-262/7.0/#sec-template-literal-lexical-components).
   */var reEsTemplate=/\$\{([^\\}]*(?:\\.[^\\}]*)*)\}/g;/** Used to detect host constructors (Safari). */var reIsHostCtor=/^\[object .+?Constructor\]$/;/** Used to detect unsigned integer values. */var reIsUint=/^(?:0|[1-9]\d*)$/;/** Used to ensure capturing order of template delimiters. */var reNoMatch=/($^)/;/** Used to match unescaped characters in compiled string literals. */var reUnescapedString=/['\n\r\u2028\u2029\\]/g;/** Used to identify `toStringTag` values of typed arrays. */var typedArrayTags={};typedArrayTags[float32Tag]=typedArrayTags[float64Tag]=typedArrayTags[int8Tag]=typedArrayTags[int16Tag]=typedArrayTags[int32Tag]=typedArrayTags[uint8Tag]=typedArrayTags[uint8ClampedTag]=typedArrayTags[uint16Tag]=typedArrayTags[uint32Tag]=true;typedArrayTags[argsTag]=typedArrayTags[arrayTag]=typedArrayTags[arrayBufferTag]=typedArrayTags[boolTag]=typedArrayTags[dataViewTag]=typedArrayTags[dateTag]=typedArrayTags[errorTag]=typedArrayTags[funcTag]=typedArrayTags[mapTag]=typedArrayTags[numberTag]=typedArrayTags[objectTag]=typedArrayTags[regexpTag]=typedArrayTags[setTag]=typedArrayTags[stringTag]=typedArrayTags[weakMapTag]=false;/** Used to escape characters for inclusion in compiled string literals. */var stringEscapes={'\\':'\\',"'":"'",'\n':'n','\r':'r',"\u2028":'u2028',"\u2029":'u2029'};/** Detect free variable `global` from Node.js. */var freeGlobal=_typeof2(commonjsGlobal)=='object'&&commonjsGlobal&&commonjsGlobal.Object===Object&&commonjsGlobal;/** Detect free variable `self`. */var freeSelf=(typeof self==="undefined"?"undefined":_typeof2(self))=='object'&&self&&self.Object===Object&&self;/** Used as a reference to the global object. */var root=freeGlobal||freeSelf||Function('return this')();/** Detect free variable `exports`. */var freeExports=exports&&!exports.nodeType&&exports;/** Detect free variable `module`. */var freeModule=freeExports&&'object'=='object'&&module&&!module.nodeType&&module;/** Detect the popular CommonJS extension `module.exports`. */var moduleExports=freeModule&&freeModule.exports===freeExports;/** Detect free variable `process` from Node.js. */var freeProcess=moduleExports&&freeGlobal.process;/** Used to access faster Node.js helpers. */var nodeUtil=function(){try{// Use `util.types` for Node.js 10+.
var types=freeModule&&freeModule.require&&freeModule.require('util').types;if(types){return types;}// Legacy `process.binding('util')` for Node.js < 10.
return freeProcess&&freeProcess.binding&&freeProcess.binding('util');}catch(e){}}();/* Node.js helper references. */var nodeIsTypedArray=nodeUtil&&nodeUtil.isTypedArray;/**
   * A faster alternative to `Function#apply`, this function invokes `func`
   * with the `this` binding of `thisArg` and the arguments of `args`.
   *
   * @private
   * @param {Function} func The function to invoke.
   * @param {*} thisArg The `this` binding of `func`.
   * @param {Array} args The arguments to invoke `func` with.
   * @returns {*} Returns the result of `func`.
   */function apply(func,thisArg,args){switch(args.length){case 0:return func.call(thisArg);case 1:return func.call(thisArg,args[0]);case 2:return func.call(thisArg,args[0],args[1]);case 3:return func.call(thisArg,args[0],args[1],args[2]);}return func.apply(thisArg,args);}/**
   * A specialized version of `_.map` for arrays without support for iteratee
   * shorthands.
   *
   * @private
   * @param {Array} [array] The array to iterate over.
   * @param {Function} iteratee The function invoked per iteration.
   * @returns {Array} Returns the new mapped array.
   */function arrayMap(array,iteratee){var index=-1,length=array==null?0:array.length,result=Array(length);while(++index<length){result[index]=iteratee(array[index],index,array);}return result;}/**
   * The base implementation of `_.times` without support for iteratee shorthands
   * or max array length checks.
   *
   * @private
   * @param {number} n The number of times to invoke `iteratee`.
   * @param {Function} iteratee The function invoked per iteration.
   * @returns {Array} Returns the array of results.
   */function baseTimes(n,iteratee){var index=-1,result=Array(n);while(++index<n){result[index]=iteratee(index);}return result;}/**
   * The base implementation of `_.unary` without support for storing metadata.
   *
   * @private
   * @param {Function} func The function to cap arguments for.
   * @returns {Function} Returns the new capped function.
   */function baseUnary(func){return function(value){return func(value);};}/**
   * The base implementation of `_.values` and `_.valuesIn` which creates an
   * array of `object` property values corresponding to the property names
   * of `props`.
   *
   * @private
   * @param {Object} object The object to query.
   * @param {Array} props The property names to get values for.
   * @returns {Object} Returns the array of property values.
   */function baseValues(object,props){return arrayMap(props,function(key){return object[key];});}/**
   * Used by `_.template` to escape characters for inclusion in compiled string literals.
   *
   * @private
   * @param {string} chr The matched character to escape.
   * @returns {string} Returns the escaped character.
   */function escapeStringChar(chr){return'\\'+stringEscapes[chr];}/**
   * Gets the value at `key` of `object`.
   *
   * @private
   * @param {Object} [object] The object to query.
   * @param {string} key The key of the property to get.
   * @returns {*} Returns the property value.
   */function getValue(object,key){return object==null?undefined:object[key];}/**
   * Creates a unary function that invokes `func` with its argument transformed.
   *
   * @private
   * @param {Function} func The function to wrap.
   * @param {Function} transform The argument transform.
   * @returns {Function} Returns the new function.
   */function overArg(func,transform){return function(arg){return func(transform(arg));};}/** Used for built-in method references. */var funcProto=Function.prototype,objectProto=Object.prototype;/** Used to detect overreaching core-js shims. */var coreJsData=root['__core-js_shared__'];/** Used to resolve the decompiled source of functions. */var funcToString=funcProto.toString;/** Used to check objects for own properties. */var hasOwnProperty=objectProto.hasOwnProperty;/** Used to detect methods masquerading as native. */var maskSrcKey=function(){var uid=/[^.]+$/.exec(coreJsData&&coreJsData.keys&&coreJsData.keys.IE_PROTO||'');return uid?'Symbol(src)_1.'+uid:'';}();/**
   * Used to resolve the
   * [`toStringTag`](http://ecma-international.org/ecma-262/7.0/#sec-object.prototype.tostring)
   * of values.
   */var nativeObjectToString=objectProto.toString;/** Used to infer the `Object` constructor. */var objectCtorString=funcToString.call(Object);/** Used to detect if a method is native. */var reIsNative=RegExp('^'+funcToString.call(hasOwnProperty).replace(reRegExpChar,'\\$&').replace(/hasOwnProperty|(function).*?(?=\\\()| for .+?(?=\\\])/g,'$1.*?')+'$');/** Built-in value references. */var Buffer=moduleExports?root.Buffer:undefined,_Symbol=root.Symbol,getPrototype=overArg(Object.getPrototypeOf,Object),propertyIsEnumerable=objectProto.propertyIsEnumerable,symToStringTag=_Symbol?_Symbol.toStringTag:undefined;var defineProperty=function(){try{var func=getNative(Object,'defineProperty');func({},'',{});return func;}catch(e){}}();/* Built-in method references for those with the same name as other `lodash` methods. */var nativeIsBuffer=Buffer?Buffer.isBuffer:undefined,nativeKeys=overArg(Object.keys,Object),nativeMax=Math.max,nativeNow=Date.now;/** Used to convert symbols to primitives and strings. */var symbolProto=_Symbol?_Symbol.prototype:undefined,symbolToString=symbolProto?symbolProto.toString:undefined;/**
   * Creates an array of the enumerable property names of the array-like `value`.
   *
   * @private
   * @param {*} value The value to query.
   * @param {boolean} inherited Specify returning inherited property names.
   * @returns {Array} Returns the array of property names.
   */function arrayLikeKeys(value,inherited){var isArr=isArray(value),isArg=!isArr&&isArguments(value),isBuff=!isArr&&!isArg&&isBuffer(value),isType=!isArr&&!isArg&&!isBuff&&isTypedArray(value),skipIndexes=isArr||isArg||isBuff||isType,result=skipIndexes?baseTimes(value.length,String):[],length=result.length;for(var key in value){if((inherited||hasOwnProperty.call(value,key))&&!(skipIndexes&&(// Safari 9 has enumerable `arguments.length` in strict mode.
key=='length'||// Node.js 0.10 has enumerable non-index properties on buffers.
isBuff&&(key=='offset'||key=='parent')||// PhantomJS 2 has enumerable non-index properties on typed arrays.
isType&&(key=='buffer'||key=='byteLength'||key=='byteOffset')||// Skip index properties.
isIndex(key,length)))){result.push(key);}}return result;}/**
   * Assigns `value` to `key` of `object` if the existing value is not equivalent
   * using [`SameValueZero`](http://ecma-international.org/ecma-262/7.0/#sec-samevaluezero)
   * for equality comparisons.
   *
   * @private
   * @param {Object} object The object to modify.
   * @param {string} key The key of the property to assign.
   * @param {*} value The value to assign.
   */function assignValue(object,key,value){var objValue=object[key];if(!(hasOwnProperty.call(object,key)&&eq(objValue,value))||value===undefined&&!(key in object)){baseAssignValue(object,key,value);}}/**
   * The base implementation of `assignValue` and `assignMergeValue` without
   * value checks.
   *
   * @private
   * @param {Object} object The object to modify.
   * @param {string} key The key of the property to assign.
   * @param {*} value The value to assign.
   */function baseAssignValue(object,key,value){if(key=='__proto__'&&defineProperty){defineProperty(object,key,{'configurable':true,'enumerable':true,'value':value,'writable':true});}else{object[key]=value;}}/**
   * The base implementation of `getTag` without fallbacks for buggy environments.
   *
   * @private
   * @param {*} value The value to query.
   * @returns {string} Returns the `toStringTag`.
   */function baseGetTag(value){if(value==null){return value===undefined?undefinedTag:nullTag;}return symToStringTag&&symToStringTag in Object(value)?getRawTag(value):objectToString(value);}/**
   * The base implementation of `_.isArguments`.
   *
   * @private
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an `arguments` object,
   */function baseIsArguments(value){return isObjectLike(value)&&baseGetTag(value)==argsTag;}/**
   * The base implementation of `_.isNative` without bad shim checks.
   *
   * @private
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a native function,
   *  else `false`.
   */function baseIsNative(value){if(!isObject(value)||isMasked(value)){return false;}var pattern=isFunction(value)?reIsNative:reIsHostCtor;return pattern.test(toSource(value));}/**
   * The base implementation of `_.isTypedArray` without Node.js optimizations.
   *
   * @private
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a typed array, else `false`.
   */function baseIsTypedArray(value){return isObjectLike(value)&&isLength(value.length)&&!!typedArrayTags[baseGetTag(value)];}/**
   * The base implementation of `_.keys` which doesn't treat sparse arrays as dense.
   *
   * @private
   * @param {Object} object The object to query.
   * @returns {Array} Returns the array of property names.
   */function baseKeys(object){if(!isPrototype(object)){return nativeKeys(object);}var result=[];for(var key in Object(object)){if(hasOwnProperty.call(object,key)&&key!='constructor'){result.push(key);}}return result;}/**
   * The base implementation of `_.keysIn` which doesn't treat sparse arrays as dense.
   *
   * @private
   * @param {Object} object The object to query.
   * @returns {Array} Returns the array of property names.
   */function baseKeysIn(object){if(!isObject(object)){return nativeKeysIn(object);}var isProto=isPrototype(object),result=[];for(var key in object){if(!(key=='constructor'&&(isProto||!hasOwnProperty.call(object,key)))){result.push(key);}}return result;}/**
   * The base implementation of `_.rest` which doesn't validate or coerce arguments.
   *
   * @private
   * @param {Function} func The function to apply a rest parameter to.
   * @param {number} [start=func.length-1] The start position of the rest parameter.
   * @returns {Function} Returns the new function.
   */function baseRest(func,start){return setToString(overRest(func,start,identity),func+'');}/**
   * The base implementation of `setToString` without support for hot loop shorting.
   *
   * @private
   * @param {Function} func The function to modify.
   * @param {Function} string The `toString` result.
   * @returns {Function} Returns `func`.
   */var baseSetToString=!defineProperty?identity:function(func,string){return defineProperty(func,'toString',{'configurable':true,'enumerable':false,'value':constant(string),'writable':true});};/**
   * The base implementation of `_.toString` which doesn't convert nullish
   * values to empty strings.
   *
   * @private
   * @param {*} value The value to process.
   * @returns {string} Returns the string.
   */function baseToString(value){// Exit early for strings to avoid a performance hit in some environments.
if(typeof value=='string'){return value;}if(isArray(value)){// Recursively convert values (susceptible to call stack limits).
return arrayMap(value,baseToString)+'';}if(isSymbol(value)){return symbolToString?symbolToString.call(value):'';}var result=value+'';return result=='0'&&1/value==-INFINITY?'-0':result;}/**
   * Copies properties of `source` to `object`.
   *
   * @private
   * @param {Object} source The object to copy properties from.
   * @param {Array} props The property identifiers to copy.
   * @param {Object} [object={}] The object to copy properties to.
   * @param {Function} [customizer] The function to customize copied values.
   * @returns {Object} Returns `object`.
   */function copyObject(source,props,object,customizer){var isNew=!object;object||(object={});var index=-1,length=props.length;while(++index<length){var key=props[index];var newValue=customizer?customizer(object[key],source[key],key,object,source):undefined;if(newValue===undefined){newValue=source[key];}if(isNew){baseAssignValue(object,key,newValue);}else{assignValue(object,key,newValue);}}return object;}/**
   * Creates a function like `_.assign`.
   *
   * @private
   * @param {Function} assigner The function to assign values.
   * @returns {Function} Returns the new assigner function.
   */function createAssigner(assigner){return baseRest(function(object,sources){var index=-1,length=sources.length,customizer=length>1?sources[length-1]:undefined,guard=length>2?sources[2]:undefined;customizer=assigner.length>3&&typeof customizer=='function'?(length--,customizer):undefined;if(guard&&isIterateeCall(sources[0],sources[1],guard)){customizer=length<3?undefined:customizer;length=1;}object=Object(object);while(++index<length){var source=sources[index];if(source){assigner(object,source,index,customizer);}}return object;});}/**
   * Used by `_.defaults` to customize its `_.assignIn` use to assign properties
   * of source objects to the destination object for all destination properties
   * that resolve to `undefined`.
   *
   * @private
   * @param {*} objValue The destination value.
   * @param {*} srcValue The source value.
   * @param {string} key The key of the property to assign.
   * @param {Object} object The parent object of `objValue`.
   * @returns {*} Returns the value to assign.
   */function customDefaultsAssignIn(objValue,srcValue,key,object){if(objValue===undefined||eq(objValue,objectProto[key])&&!hasOwnProperty.call(object,key)){return srcValue;}return objValue;}/**
   * Gets the native function at `key` of `object`.
   *
   * @private
   * @param {Object} object The object to query.
   * @param {string} key The key of the method to get.
   * @returns {*} Returns the function if it's native, else `undefined`.
   */function getNative(object,key){var value=getValue(object,key);return baseIsNative(value)?value:undefined;}/**
   * A specialized version of `baseGetTag` which ignores `Symbol.toStringTag` values.
   *
   * @private
   * @param {*} value The value to query.
   * @returns {string} Returns the raw `toStringTag`.
   */function getRawTag(value){var isOwn=hasOwnProperty.call(value,symToStringTag),tag=value[symToStringTag];try{value[symToStringTag]=undefined;var unmasked=true;}catch(e){}var result=nativeObjectToString.call(value);if(unmasked){if(isOwn){value[symToStringTag]=tag;}else{delete value[symToStringTag];}}return result;}/**
   * Checks if `value` is a valid array-like index.
   *
   * @private
   * @param {*} value The value to check.
   * @param {number} [length=MAX_SAFE_INTEGER] The upper bounds of a valid index.
   * @returns {boolean} Returns `true` if `value` is a valid index, else `false`.
   */function isIndex(value,length){var type=_typeof2(value);length=length==null?MAX_SAFE_INTEGER:length;return!!length&&(type=='number'||type!='symbol'&&reIsUint.test(value))&&value>-1&&value%1==0&&value<length;}/**
   * Checks if the given arguments are from an iteratee call.
   *
   * @private
   * @param {*} value The potential iteratee value argument.
   * @param {*} index The potential iteratee index or key argument.
   * @param {*} object The potential iteratee object argument.
   * @returns {boolean} Returns `true` if the arguments are from an iteratee call,
   *  else `false`.
   */function isIterateeCall(value,index,object){if(!isObject(object)){return false;}var type=_typeof2(index);if(type=='number'?isArrayLike(object)&&isIndex(index,object.length):type=='string'&&index in object){return eq(object[index],value);}return false;}/**
   * Checks if `func` has its source masked.
   *
   * @private
   * @param {Function} func The function to check.
   * @returns {boolean} Returns `true` if `func` is masked, else `false`.
   */function isMasked(func){return!!maskSrcKey&&maskSrcKey in func;}/**
   * Checks if `value` is likely a prototype object.
   *
   * @private
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a prototype, else `false`.
   */function isPrototype(value){var Ctor=value&&value.constructor,proto=typeof Ctor=='function'&&Ctor.prototype||objectProto;return value===proto;}/**
   * This function is like
   * [`Object.keys`](http://ecma-international.org/ecma-262/7.0/#sec-object.keys)
   * except that it includes inherited enumerable properties.
   *
   * @private
   * @param {Object} object The object to query.
   * @returns {Array} Returns the array of property names.
   */function nativeKeysIn(object){var result=[];if(object!=null){for(var key in Object(object)){result.push(key);}}return result;}/**
   * Converts `value` to a string using `Object.prototype.toString`.
   *
   * @private
   * @param {*} value The value to convert.
   * @returns {string} Returns the converted string.
   */function objectToString(value){return nativeObjectToString.call(value);}/**
   * A specialized version of `baseRest` which transforms the rest array.
   *
   * @private
   * @param {Function} func The function to apply a rest parameter to.
   * @param {number} [start=func.length-1] The start position of the rest parameter.
   * @param {Function} transform The rest array transform.
   * @returns {Function} Returns the new function.
   */function overRest(func,start,transform){start=nativeMax(start===undefined?func.length-1:start,0);return function(){var args=arguments,index=-1,length=nativeMax(args.length-start,0),array=Array(length);while(++index<length){array[index]=args[start+index];}index=-1;var otherArgs=Array(start+1);while(++index<start){otherArgs[index]=args[index];}otherArgs[start]=transform(array);return apply(func,this,otherArgs);};}/**
   * Sets the `toString` method of `func` to return `string`.
   *
   * @private
   * @param {Function} func The function to modify.
   * @param {Function} string The `toString` result.
   * @returns {Function} Returns `func`.
   */var setToString=shortOut(baseSetToString);/**
   * Creates a function that'll short out and invoke `identity` instead
   * of `func` when it's called `HOT_COUNT` or more times in `HOT_SPAN`
   * milliseconds.
   *
   * @private
   * @param {Function} func The function to restrict.
   * @returns {Function} Returns the new shortable function.
   */function shortOut(func){var count=0,lastCalled=0;return function(){var stamp=nativeNow(),remaining=HOT_SPAN-(stamp-lastCalled);lastCalled=stamp;if(remaining>0){if(++count>=HOT_COUNT){return arguments[0];}}else{count=0;}return func.apply(undefined,arguments);};}/**
   * Converts `func` to its source code.
   *
   * @private
   * @param {Function} func The function to convert.
   * @returns {string} Returns the source code.
   */function toSource(func){if(func!=null){try{return funcToString.call(func);}catch(e){}try{return func+'';}catch(e){}}return'';}/**
   * Performs a
   * [`SameValueZero`](http://ecma-international.org/ecma-262/7.0/#sec-samevaluezero)
   * comparison between two values to determine if they are equivalent.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to compare.
   * @param {*} other The other value to compare.
   * @returns {boolean} Returns `true` if the values are equivalent, else `false`.
   * @example
   *
   * var object = { 'a': 1 };
   * var other = { 'a': 1 };
   *
   * _.eq(object, object);
   * // => true
   *
   * _.eq(object, other);
   * // => false
   *
   * _.eq('a', 'a');
   * // => true
   *
   * _.eq('a', Object('a'));
   * // => false
   *
   * _.eq(NaN, NaN);
   * // => true
   */function eq(value,other){return value===other||value!==value&&other!==other;}/**
   * Checks if `value` is likely an `arguments` object.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an `arguments` object,
   *  else `false`.
   * @example
   *
   * _.isArguments(function() { return arguments; }());
   * // => true
   *
   * _.isArguments([1, 2, 3]);
   * // => false
   */var isArguments=baseIsArguments(function(){return arguments;}())?baseIsArguments:function(value){return isObjectLike(value)&&hasOwnProperty.call(value,'callee')&&!propertyIsEnumerable.call(value,'callee');};/**
   * Checks if `value` is classified as an `Array` object.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an array, else `false`.
   * @example
   *
   * _.isArray([1, 2, 3]);
   * // => true
   *
   * _.isArray(document.body.children);
   * // => false
   *
   * _.isArray('abc');
   * // => false
   *
   * _.isArray(_.noop);
   * // => false
   */var isArray=Array.isArray;/**
   * Checks if `value` is array-like. A value is considered array-like if it's
   * not a function and has a `value.length` that's an integer greater than or
   * equal to `0` and less than or equal to `Number.MAX_SAFE_INTEGER`.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is array-like, else `false`.
   * @example
   *
   * _.isArrayLike([1, 2, 3]);
   * // => true
   *
   * _.isArrayLike(document.body.children);
   * // => true
   *
   * _.isArrayLike('abc');
   * // => true
   *
   * _.isArrayLike(_.noop);
   * // => false
   */function isArrayLike(value){return value!=null&&isLength(value.length)&&!isFunction(value);}/**
   * Checks if `value` is a buffer.
   *
   * @static
   * @memberOf _
   * @since 4.3.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a buffer, else `false`.
   * @example
   *
   * _.isBuffer(new Buffer(2));
   * // => true
   *
   * _.isBuffer(new Uint8Array(2));
   * // => false
   */var isBuffer=nativeIsBuffer||stubFalse;/**
   * Checks if `value` is an `Error`, `EvalError`, `RangeError`, `ReferenceError`,
   * `SyntaxError`, `TypeError`, or `URIError` object.
   *
   * @static
   * @memberOf _
   * @since 3.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an error object, else `false`.
   * @example
   *
   * _.isError(new Error);
   * // => true
   *
   * _.isError(Error);
   * // => false
   */function isError(value){if(!isObjectLike(value)){return false;}var tag=baseGetTag(value);return tag==errorTag||tag==domExcTag||typeof value.message=='string'&&typeof value.name=='string'&&!isPlainObject(value);}/**
   * Checks if `value` is classified as a `Function` object.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a function, else `false`.
   * @example
   *
   * _.isFunction(_);
   * // => true
   *
   * _.isFunction(/abc/);
   * // => false
   */function isFunction(value){if(!isObject(value)){return false;}// The use of `Object#toString` avoids issues with the `typeof` operator
// in Safari 9 which returns 'object' for typed arrays and other constructors.
var tag=baseGetTag(value);return tag==funcTag||tag==genTag||tag==asyncTag||tag==proxyTag;}/**
   * Checks if `value` is a valid array-like length.
   *
   * **Note:** This method is loosely based on
   * [`ToLength`](http://ecma-international.org/ecma-262/7.0/#sec-tolength).
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a valid length, else `false`.
   * @example
   *
   * _.isLength(3);
   * // => true
   *
   * _.isLength(Number.MIN_VALUE);
   * // => false
   *
   * _.isLength(Infinity);
   * // => false
   *
   * _.isLength('3');
   * // => false
   */function isLength(value){return typeof value=='number'&&value>-1&&value%1==0&&value<=MAX_SAFE_INTEGER;}/**
   * Checks if `value` is the
   * [language type](http://www.ecma-international.org/ecma-262/7.0/#sec-ecmascript-language-types)
   * of `Object`. (e.g. arrays, functions, objects, regexes, `new Number(0)`, and `new String('')`)
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an object, else `false`.
   * @example
   *
   * _.isObject({});
   * // => true
   *
   * _.isObject([1, 2, 3]);
   * // => true
   *
   * _.isObject(_.noop);
   * // => true
   *
   * _.isObject(null);
   * // => false
   */function isObject(value){var type=_typeof2(value);return value!=null&&(type=='object'||type=='function');}/**
   * Checks if `value` is object-like. A value is object-like if it's not `null`
   * and has a `typeof` result of "object".
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is object-like, else `false`.
   * @example
   *
   * _.isObjectLike({});
   * // => true
   *
   * _.isObjectLike([1, 2, 3]);
   * // => true
   *
   * _.isObjectLike(_.noop);
   * // => false
   *
   * _.isObjectLike(null);
   * // => false
   */function isObjectLike(value){return value!=null&&_typeof2(value)=='object';}/**
   * Checks if `value` is a plain object, that is, an object created by the
   * `Object` constructor or one with a `[[Prototype]]` of `null`.
   *
   * @static
   * @memberOf _
   * @since 0.8.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a plain object, else `false`.
   * @example
   *
   * function Foo() {
   *   this.a = 1;
   * }
   *
   * _.isPlainObject(new Foo);
   * // => false
   *
   * _.isPlainObject([1, 2, 3]);
   * // => false
   *
   * _.isPlainObject({ 'x': 0, 'y': 0 });
   * // => true
   *
   * _.isPlainObject(Object.create(null));
   * // => true
   */function isPlainObject(value){if(!isObjectLike(value)||baseGetTag(value)!=objectTag){return false;}var proto=getPrototype(value);if(proto===null){return true;}var Ctor=hasOwnProperty.call(proto,'constructor')&&proto.constructor;return typeof Ctor=='function'&&Ctor instanceof Ctor&&funcToString.call(Ctor)==objectCtorString;}/**
   * Checks if `value` is classified as a `Symbol` primitive or object.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a symbol, else `false`.
   * @example
   *
   * _.isSymbol(Symbol.iterator);
   * // => true
   *
   * _.isSymbol('abc');
   * // => false
   */function isSymbol(value){return _typeof2(value)=='symbol'||isObjectLike(value)&&baseGetTag(value)==symbolTag;}/**
   * Checks if `value` is classified as a typed array.
   *
   * @static
   * @memberOf _
   * @since 3.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a typed array, else `false`.
   * @example
   *
   * _.isTypedArray(new Uint8Array);
   * // => true
   *
   * _.isTypedArray([]);
   * // => false
   */var isTypedArray=nodeIsTypedArray?baseUnary(nodeIsTypedArray):baseIsTypedArray;/**
   * Converts `value` to a string. An empty string is returned for `null`
   * and `undefined` values. The sign of `-0` is preserved.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to convert.
   * @returns {string} Returns the converted string.
   * @example
   *
   * _.toString(null);
   * // => ''
   *
   * _.toString(-0);
   * // => '-0'
   *
   * _.toString([1, 2, 3]);
   * // => '1,2,3'
   */function toString(value){return value==null?'':baseToString(value);}/**
   * This method is like `_.assignIn` except that it accepts `customizer`
   * which is invoked to produce the assigned values. If `customizer` returns
   * `undefined`, assignment is handled by the method instead. The `customizer`
   * is invoked with five arguments: (objValue, srcValue, key, object, source).
   *
   * **Note:** This method mutates `object`.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @alias extendWith
   * @category Object
   * @param {Object} object The destination object.
   * @param {...Object} sources The source objects.
   * @param {Function} [customizer] The function to customize assigned values.
   * @returns {Object} Returns `object`.
   * @see _.assignWith
   * @example
   *
   * function customizer(objValue, srcValue) {
   *   return _.isUndefined(objValue) ? srcValue : objValue;
   * }
   *
   * var defaults = _.partialRight(_.assignInWith, customizer);
   *
   * defaults({ 'a': 1 }, { 'b': 2 }, { 'a': 3 });
   * // => { 'a': 1, 'b': 2 }
   */var assignInWith=createAssigner(function(object,source,srcIndex,customizer){copyObject(source,keysIn(source),object,customizer);});/**
   * Creates an array of the own enumerable property names of `object`.
   *
   * **Note:** Non-object values are coerced to objects. See the
   * [ES spec](http://ecma-international.org/ecma-262/7.0/#sec-object.keys)
   * for more details.
   *
   * @static
   * @since 0.1.0
   * @memberOf _
   * @category Object
   * @param {Object} object The object to query.
   * @returns {Array} Returns the array of property names.
   * @example
   *
   * function Foo() {
   *   this.a = 1;
   *   this.b = 2;
   * }
   *
   * Foo.prototype.c = 3;
   *
   * _.keys(new Foo);
   * // => ['a', 'b'] (iteration order is not guaranteed)
   *
   * _.keys('hi');
   * // => ['0', '1']
   */function keys(object){return isArrayLike(object)?arrayLikeKeys(object):baseKeys(object);}/**
   * Creates an array of the own and inherited enumerable property names of `object`.
   *
   * **Note:** Non-object values are coerced to objects.
   *
   * @static
   * @memberOf _
   * @since 3.0.0
   * @category Object
   * @param {Object} object The object to query.
   * @returns {Array} Returns the array of property names.
   * @example
   *
   * function Foo() {
   *   this.a = 1;
   *   this.b = 2;
   * }
   *
   * Foo.prototype.c = 3;
   *
   * _.keysIn(new Foo);
   * // => ['a', 'b', 'c'] (iteration order is not guaranteed)
   */function keysIn(object){return isArrayLike(object)?arrayLikeKeys(object,true):baseKeysIn(object);}/**
   * Creates a compiled template function that can interpolate data properties
   * in "interpolate" delimiters, HTML-escape interpolated data properties in
   * "escape" delimiters, and execute JavaScript in "evaluate" delimiters. Data
   * properties may be accessed as free variables in the template. If a setting
   * object is given, it takes precedence over `_.templateSettings` values.
   *
   * **Note:** In the development build `_.template` utilizes
   * [sourceURLs](http://www.html5rocks.com/en/tutorials/developertools/sourcemaps/#toc-sourceurl)
   * for easier debugging.
   *
   * For more information on precompiling templates see
   * [lodash's custom builds documentation](https://lodash.com/custom-builds).
   *
   * For more information on Chrome extension sandboxes see
   * [Chrome's extensions documentation](https://developer.chrome.com/extensions/sandboxingEval).
   *
   * @static
   * @since 0.1.0
   * @memberOf _
   * @category String
   * @param {string} [string=''] The template string.
   * @param {Object} [options={}] The options object.
   * @param {RegExp} [options.escape=_.templateSettings.escape]
   *  The HTML "escape" delimiter.
   * @param {RegExp} [options.evaluate=_.templateSettings.evaluate]
   *  The "evaluate" delimiter.
   * @param {Object} [options.imports=_.templateSettings.imports]
   *  An object to import into the template as free variables.
   * @param {RegExp} [options.interpolate=_.templateSettings.interpolate]
   *  The "interpolate" delimiter.
   * @param {string} [options.sourceURL='templateSources[n]']
   *  The sourceURL of the compiled template.
   * @param {string} [options.variable='obj']
   *  The data object variable name.
   * @param- {Object} [guard] Enables use as an iteratee for methods like `_.map`.
   * @returns {Function} Returns the compiled template function.
   * @example
   *
   * // Use the "interpolate" delimiter to create a compiled template.
   * var compiled = _.template('hello <%= user %>!');
   * compiled({ 'user': 'fred' });
   * // => 'hello fred!'
   *
   * // Use the HTML "escape" delimiter to escape data property values.
   * var compiled = _.template('<b><%- value %></b>');
   * compiled({ 'value': '<script>' });
   * // => '<b>&lt;script&gt;</b>'
   *
   * // Use the "evaluate" delimiter to execute JavaScript and generate HTML.
   * var compiled = _.template('<% _.forEach(users, function(user) { %><li><%- user %></li><% }); %>');
   * compiled({ 'users': ['fred', 'barney'] });
   * // => '<li>fred</li><li>barney</li>'
   *
   * // Use the internal `print` function in "evaluate" delimiters.
   * var compiled = _.template('<% print("hello " + user); %>!');
   * compiled({ 'user': 'barney' });
   * // => 'hello barney!'
   *
   * // Use the ES template literal delimiter as an "interpolate" delimiter.
   * // Disable support by replacing the "interpolate" delimiter.
   * var compiled = _.template('hello ${ user }!');
   * compiled({ 'user': 'pebbles' });
   * // => 'hello pebbles!'
   *
   * // Use backslashes to treat delimiters as plain text.
   * var compiled = _.template('<%= "\\<%- value %\\>" %>');
   * compiled({ 'value': 'ignored' });
   * // => '<%- value %>'
   *
   * // Use the `imports` option to import `jQuery` as `jq`.
   * var text = '<% jq.each(users, function(user) { %><li><%- user %></li><% }); %>';
   * var compiled = _.template(text, { 'imports': { 'jq': jQuery } });
   * compiled({ 'users': ['fred', 'barney'] });
   * // => '<li>fred</li><li>barney</li>'
   *
   * // Use the `sourceURL` option to specify a custom sourceURL for the template.
   * var compiled = _.template('hello <%= user %>!', { 'sourceURL': '/basic/greeting.jst' });
   * compiled(data);
   * // => Find the source of "greeting.jst" under the Sources tab or Resources panel of the web inspector.
   *
   * // Use the `variable` option to ensure a with-statement isn't used in the compiled template.
   * var compiled = _.template('hi <%= data.user %>!', { 'variable': 'data' });
   * compiled.source;
   * // => function(data) {
   * //   var __t, __p = '';
   * //   __p += 'hi ' + ((__t = ( data.user )) == null ? '' : __t) + '!';
   * //   return __p;
   * // }
   *
   * // Use custom template delimiters.
   * _.templateSettings.interpolate = /{{([\s\S]+?)}}/g;
   * var compiled = _.template('hello {{ user }}!');
   * compiled({ 'user': 'mustache' });
   * // => 'hello mustache!'
   *
   * // Use the `source` property to inline compiled templates for meaningful
   * // line numbers in error messages and stack traces.
   * fs.writeFileSync(path.join(process.cwd(), 'jst.js'), '\
   *   var JST = {\
   *     "main": ' + _.template(mainText).source + '\
   *   };\
   * ');
   */function template(string,options,guard){// Based on John Resig's `tmpl` implementation
// (http://ejohn.org/blog/javascript-micro-templating/)
// and Laura Doktorova's doT.js (https://github.com/olado/doT).
var settings=lodash_templatesettings.imports._.templateSettings||lodash_templatesettings;if(guard&&isIterateeCall(string,options,guard)){options=undefined;}string=toString(string);options=assignInWith({},options,settings,customDefaultsAssignIn);var imports=assignInWith({},options.imports,settings.imports,customDefaultsAssignIn),importsKeys=keys(imports),importsValues=baseValues(imports,importsKeys);var isEscaping,isEvaluating,index=0,interpolate=options.interpolate||reNoMatch,source="__p += '";// Compile the regexp to match each delimiter.
var reDelimiters=RegExp((options.escape||reNoMatch).source+'|'+interpolate.source+'|'+(interpolate===lodash__reinterpolate?reEsTemplate:reNoMatch).source+'|'+(options.evaluate||reNoMatch).source+'|$','g');// Use a sourceURL for easier debugging.
// The sourceURL gets injected into the source that's eval-ed, so be careful
// with lookup (in case of e.g. prototype pollution), and strip newlines if any.
// A newline wouldn't be a valid sourceURL anyway, and it'd enable code injection.
var sourceURL=hasOwnProperty.call(options,'sourceURL')?'//# sourceURL='+(options.sourceURL+'').replace(/[\r\n]/g,' ')+'\n':'';string.replace(reDelimiters,function(match,escapeValue,interpolateValue,esTemplateValue,evaluateValue,offset){interpolateValue||(interpolateValue=esTemplateValue);// Escape characters that can't be included in string literals.
source+=string.slice(index,offset).replace(reUnescapedString,escapeStringChar);// Replace delimiters with snippets.
if(escapeValue){isEscaping=true;source+="' +\n__e("+escapeValue+") +\n'";}if(evaluateValue){isEvaluating=true;source+="';\n"+evaluateValue+";\n__p += '";}if(interpolateValue){source+="' +\n((__t = ("+interpolateValue+")) == null ? '' : __t) +\n'";}index=offset+match.length;// The JS engine embedded in Adobe products needs `match` returned in
// order to produce the correct `offset` value.
return match;});source+="';\n";// If `variable` is not specified wrap a with-statement around the generated
// code to add the data object to the top of the scope chain.
// Like with sourceURL, we take care to not check the option's prototype,
// as this configuration is a code injection vector.
var variable=hasOwnProperty.call(options,'variable')&&options.variable;if(!variable){source='with (obj) {\n'+source+'\n}\n';}// Cleanup code by stripping empty strings.
source=(isEvaluating?source.replace(reEmptyStringLeading,''):source).replace(reEmptyStringMiddle,'$1').replace(reEmptyStringTrailing,'$1;');// Frame code as the function body.
source='function('+(variable||'obj')+') {\n'+(variable?'':'obj || (obj = {});\n')+"var __t, __p = ''"+(isEscaping?', __e = _.escape':'')+(isEvaluating?', __j = Array.prototype.join;\n'+"function print() { __p += __j.call(arguments, '') }\n":';\n')+source+'return __p\n}';var result=attempt(function(){return Function(importsKeys,sourceURL+'return '+source).apply(undefined,importsValues);});// Provide the compiled function's source by its `toString` method or
// the `source` property as a convenience for inlining compiled templates.
result.source=source;if(isError(result)){throw result;}return result;}/**
   * Attempts to invoke `func`, returning either the result or the caught error
   * object. Any additional arguments are provided to `func` when it's invoked.
   *
   * @static
   * @memberOf _
   * @since 3.0.0
   * @category Util
   * @param {Function} func The function to attempt.
   * @param {...*} [args] The arguments to invoke `func` with.
   * @returns {*} Returns the `func` result or error object.
   * @example
   *
   * // Avoid throwing errors for invalid selectors.
   * var elements = _.attempt(function(selector) {
   *   return document.querySelectorAll(selector);
   * }, '>_>');
   *
   * if (_.isError(elements)) {
   *   elements = [];
   * }
   */var attempt=baseRest(function(func,args){try{return apply(func,undefined,args);}catch(e){return isError(e)?e:new Error(e);}});/**
   * Creates a function that returns `value`.
   *
   * @static
   * @memberOf _
   * @since 2.4.0
   * @category Util
   * @param {*} value The value to return from the new function.
   * @returns {Function} Returns the new constant function.
   * @example
   *
   * var objects = _.times(2, _.constant({ 'a': 1 }));
   *
   * console.log(objects);
   * // => [{ 'a': 1 }, { 'a': 1 }]
   *
   * console.log(objects[0] === objects[1]);
   * // => true
   */function constant(value){return function(){return value;};}/**
   * This method returns the first argument it receives.
   *
   * @static
   * @since 0.1.0
   * @memberOf _
   * @category Util
   * @param {*} value Any value.
   * @returns {*} Returns `value`.
   * @example
   *
   * var object = { 'a': 1 };
   *
   * console.log(_.identity(object) === object);
   * // => true
   */function identity(value){return value;}/**
   * This method returns `false`.
   *
   * @static
   * @memberOf _
   * @since 4.13.0
   * @category Util
   * @returns {boolean} Returns `false`.
   * @example
   *
   * _.times(2, _.stubFalse);
   * // => [false, false]
   */function stubFalse(){return false;}module.exports=template;});// We need a vue instance to handle reactivity
var vm=null;// The plugin
var VueTranslate={// Install the method
install:function install(Vue){var _Vue$mixin;var version=Vue.version[0];if(!vm){vm=new Vue({data:function data(){return{current:'',locales:{}};},computed:{// Current selected language
lang:function lang(){return this.current;},// Current locale values
locale:function locale(){if(!this.locales[this.current])return null;return this.locales[this.current];}},methods:{// Set a language as current
setLang:function setLang(val){if(this.current!==val){if(this.current===''){this.$emit('language:init',val);}else{this.$emit('language:changed',val);}}this.current=val;this.$emit('language:modified',val);},// Set a locale tu use
setLocales:function setLocales(locales){if(!locales){return;}var newLocale=Object.create(this.locales);for(var key in locales){if(!locales.hasOwnProperty(key)){continue;}if(!newLocale[key]){newLocale[key]={};}Vue.util.extend(newLocale[key],locales[key]);}this.locales=Object.create(newLocale);this.$emit('locales:loaded',locales);},text:function text(t,params){if(params){return lodash_template(this.locale[t]||t,{interpolate:/{([\s\S]+?)}/g})(params);}return this.locale[t]||t;}}});Vue.prototype.$translate=vm;}// Mixin to read locales and add the translation method and directive
Vue.mixin((_Vue$mixin={},_defineProperty(_Vue$mixin,version==='1'?'init':'beforeCreate',function(){this.$translate.setLocales(this.$options.locales);}),_defineProperty(_Vue$mixin,"methods",{// An alias for the .$translate.text method
t:function t(_t,p){return this.$translate.text(_t,p);}}),_defineProperty(_Vue$mixin,"directives",{translate:function translate(el){if(!el.$translateKey)el.$translateKey=el.innerText;var text=this.$translate.text(el.$translateKey);el.innerText=text;}.bind(vm)}),_Vue$mixin));// Global method for loading locales
Vue.locales=function(locales){vm.$translate.setLocales(locales);};// Global method for setting languages
Vue.lang=function(lang){vm.$translate.setLang(lang);};}};/**
   * vuex v3.1.2
   * (c) 2019 Evan You
   * @license MIT
   */function applyMixin(Vue){var version=Number(Vue.version.split('.')[0]);if(version>=2){Vue.mixin({beforeCreate:vuexInit});}else{// override init and inject vuex init procedure
// for 1.x backwards compatibility.
var _init=Vue.prototype._init;Vue.prototype._init=function(options){if(options===void 0)options={};options.init=options.init?[vuexInit].concat(options.init):vuexInit;_init.call(this,options);};}/**
     * Vuex init hook, injected into each instances init hooks list.
     */function vuexInit(){var options=this.$options;// store injection
if(options.store){this.$store=typeof options.store==='function'?options.store():options.store;}else if(options.parent&&options.parent.$store){this.$store=options.parent.$store;}}}var target$2=typeof window!=='undefined'?window:typeof global!=='undefined'?global:{};var devtoolHook=target$2.__VUE_DEVTOOLS_GLOBAL_HOOK__;function devtoolPlugin(store){if(!devtoolHook){return;}store._devtoolHook=devtoolHook;devtoolHook.emit('vuex:init',store);devtoolHook.on('vuex:travel-to-state',function(targetState){store.replaceState(targetState);});store.subscribe(function(mutation,state){devtoolHook.emit('vuex:mutation',mutation,state);});}/**
   * Get the first item that pass the test
   * by second argument function
   *
   * @param {Array} list
   * @param {Function} f
   * @return {*}
   */ /**
   * forEach for object
   */function forEachValue(obj,fn){Object.keys(obj).forEach(function(key){return fn(obj[key],key);});}function isObject$1(obj){return obj!==null&&_typeof2(obj)==='object';}function isPromise$1(val){return val&&typeof val.then==='function';}function partial(fn,arg){return function(){return fn(arg);};}// Base data struct for store's module, package with some attribute and method
var Module=function Module(rawModule,runtime){this.runtime=runtime;// Store some children item
this._children=Object.create(null);// Store the origin module object which passed by programmer
this._rawModule=rawModule;var rawState=rawModule.state;// Store the origin module's state
this.state=(typeof rawState==='function'?rawState():rawState)||{};};var prototypeAccessors$1={namespaced:{configurable:true}};prototypeAccessors$1.namespaced.get=function(){return!!this._rawModule.namespaced;};Module.prototype.addChild=function addChild(key,module){this._children[key]=module;};Module.prototype.removeChild=function removeChild(key){delete this._children[key];};Module.prototype.getChild=function getChild(key){return this._children[key];};Module.prototype.update=function update(rawModule){this._rawModule.namespaced=rawModule.namespaced;if(rawModule.actions){this._rawModule.actions=rawModule.actions;}if(rawModule.mutations){this._rawModule.mutations=rawModule.mutations;}if(rawModule.getters){this._rawModule.getters=rawModule.getters;}};Module.prototype.forEachChild=function forEachChild(fn){forEachValue(this._children,fn);};Module.prototype.forEachGetter=function forEachGetter(fn){if(this._rawModule.getters){forEachValue(this._rawModule.getters,fn);}};Module.prototype.forEachAction=function forEachAction(fn){if(this._rawModule.actions){forEachValue(this._rawModule.actions,fn);}};Module.prototype.forEachMutation=function forEachMutation(fn){if(this._rawModule.mutations){forEachValue(this._rawModule.mutations,fn);}};Object.defineProperties(Module.prototype,prototypeAccessors$1);var ModuleCollection=function ModuleCollection(rawRootModule){// register root module (Vuex.Store options)
this.register([],rawRootModule,false);};ModuleCollection.prototype.get=function get(path){return path.reduce(function(module,key){return module.getChild(key);},this.root);};ModuleCollection.prototype.getNamespace=function getNamespace(path){var module=this.root;return path.reduce(function(namespace,key){module=module.getChild(key);return namespace+(module.namespaced?key+'/':'');},'');};ModuleCollection.prototype.update=function update$1(rawRootModule){update([],this.root,rawRootModule);};ModuleCollection.prototype.register=function register(path,rawModule,runtime){var this$1=this;if(runtime===void 0)runtime=true;var newModule=new Module(rawModule,runtime);if(path.length===0){this.root=newModule;}else{var parent=this.get(path.slice(0,-1));parent.addChild(path[path.length-1],newModule);}// register nested modules
if(rawModule.modules){forEachValue(rawModule.modules,function(rawChildModule,key){this$1.register(path.concat(key),rawChildModule,runtime);});}};ModuleCollection.prototype.unregister=function unregister(path){var parent=this.get(path.slice(0,-1));var key=path[path.length-1];if(!parent.getChild(key).runtime){return;}parent.removeChild(key);};function update(path,targetModule,newModule){// update target module
targetModule.update(newModule);// update nested modules
if(newModule.modules){for(var key in newModule.modules){if(!targetModule.getChild(key)){return;}update(path.concat(key),targetModule.getChild(key),newModule.modules[key]);}}}var Vue$1;// bind on install
var Store=function Store(options){var this$1=this;if(options===void 0)options={};// Auto install if it is not done yet and `window` has `Vue`.
// To allow users to avoid auto-installation in some cases,
// this code should be placed here. See #731
if(!Vue$1&&typeof window!=='undefined'&&window.Vue){install(window.Vue);}var plugins=options.plugins;if(plugins===void 0)plugins=[];var strict=options.strict;if(strict===void 0)strict=false;// store internal state
this._committing=false;this._actions=Object.create(null);this._actionSubscribers=[];this._mutations=Object.create(null);this._wrappedGetters=Object.create(null);this._modules=new ModuleCollection(options);this._modulesNamespaceMap=Object.create(null);this._subscribers=[];this._watcherVM=new Vue$1();this._makeLocalGettersCache=Object.create(null);// bind commit and dispatch to self
var store=this;var ref=this;var dispatch=ref.dispatch;var commit=ref.commit;this.dispatch=function boundDispatch(type,payload){return dispatch.call(store,type,payload);};this.commit=function boundCommit(type,payload,options){return commit.call(store,type,payload,options);};// strict mode
this.strict=strict;var state=this._modules.root.state;// init root module.
// this also recursively registers all sub-modules
// and collects all module getters inside this._wrappedGetters
installModule(this,state,[],this._modules.root);// initialize the store vm, which is responsible for the reactivity
// (also registers _wrappedGetters as computed properties)
resetStoreVM(this,state);// apply plugins
plugins.forEach(function(plugin){return plugin(this$1);});var useDevtools=options.devtools!==undefined?options.devtools:Vue$1.config.devtools;if(useDevtools){devtoolPlugin(this);}};var prototypeAccessors$1$1={state:{configurable:true}};prototypeAccessors$1$1.state.get=function(){return this._vm._data.$$state;};prototypeAccessors$1$1.state.set=function(v){};Store.prototype.commit=function commit(_type,_payload,_options){var this$1=this;// check object-style commit
var ref=unifyObjectStyle(_type,_payload,_options);var type=ref.type;var payload=ref.payload;var mutation={type:type,payload:payload};var entry=this._mutations[type];if(!entry){return;}this._withCommit(function(){entry.forEach(function commitIterator(handler){handler(payload);});});this._subscribers.forEach(function(sub){return sub(mutation,this$1.state);});};Store.prototype.dispatch=function dispatch(_type,_payload){var this$1=this;// check object-style dispatch
var ref=unifyObjectStyle(_type,_payload);var type=ref.type;var payload=ref.payload;var action={type:type,payload:payload};var entry=this._actions[type];if(!entry){return;}try{this._actionSubscribers.filter(function(sub){return sub.before;}).forEach(function(sub){return sub.before(action,this$1.state);});}catch(e){}var result=entry.length>1?Promise.all(entry.map(function(handler){return handler(payload);})):entry[0](payload);return result.then(function(res){try{this$1._actionSubscribers.filter(function(sub){return sub.after;}).forEach(function(sub){return sub.after(action,this$1.state);});}catch(e){}return res;});};Store.prototype.subscribe=function subscribe(fn){return genericSubscribe(fn,this._subscribers);};Store.prototype.subscribeAction=function subscribeAction(fn){var subs=typeof fn==='function'?{before:fn}:fn;return genericSubscribe(subs,this._actionSubscribers);};Store.prototype.watch=function watch(getter,cb,options){var this$1=this;return this._watcherVM.$watch(function(){return getter(this$1.state,this$1.getters);},cb,options);};Store.prototype.replaceState=function replaceState(state){var this$1=this;this._withCommit(function(){this$1._vm._data.$$state=state;});};Store.prototype.registerModule=function registerModule(path,rawModule,options){if(options===void 0)options={};if(typeof path==='string'){path=[path];}this._modules.register(path,rawModule);installModule(this,this.state,path,this._modules.get(path),options.preserveState);// reset store to update getters...
resetStoreVM(this,this.state);};Store.prototype.unregisterModule=function unregisterModule(path){var this$1=this;if(typeof path==='string'){path=[path];}this._modules.unregister(path);this._withCommit(function(){var parentState=getNestedState(this$1.state,path.slice(0,-1));Vue$1["delete"](parentState,path[path.length-1]);});resetStore(this);};Store.prototype.hotUpdate=function hotUpdate(newOptions){this._modules.update(newOptions);resetStore(this,true);};Store.prototype._withCommit=function _withCommit(fn){var committing=this._committing;this._committing=true;fn();this._committing=committing;};Object.defineProperties(Store.prototype,prototypeAccessors$1$1);function genericSubscribe(fn,subs){if(subs.indexOf(fn)<0){subs.push(fn);}return function(){var i=subs.indexOf(fn);if(i>-1){subs.splice(i,1);}};}function resetStore(store,hot){store._actions=Object.create(null);store._mutations=Object.create(null);store._wrappedGetters=Object.create(null);store._modulesNamespaceMap=Object.create(null);var state=store.state;// init all modules
installModule(store,state,[],store._modules.root,true);// reset vm
resetStoreVM(store,state,hot);}function resetStoreVM(store,state,hot){var oldVm=store._vm;// bind store public getters
store.getters={};// reset local getters cache
store._makeLocalGettersCache=Object.create(null);var wrappedGetters=store._wrappedGetters;var computed={};forEachValue(wrappedGetters,function(fn,key){// use computed to leverage its lazy-caching mechanism
// direct inline function use will lead to closure preserving oldVm.
// using partial to return function with only arguments preserved in closure environment.
computed[key]=partial(fn,store);Object.defineProperty(store.getters,key,{get:function get(){return store._vm[key];},enumerable:true// for local getters
});});// use a Vue instance to store the state tree
// suppress warnings just in case the user has added
// some funky global mixins
var silent=Vue$1.config.silent;Vue$1.config.silent=true;store._vm=new Vue$1({data:{$$state:state},computed:computed});Vue$1.config.silent=silent;// enable strict mode for new vm
if(store.strict){enableStrictMode(store);}if(oldVm){if(hot){// dispatch changes in all subscribed watchers
// to force getter re-evaluation for hot reloading.
store._withCommit(function(){oldVm._data.$$state=null;});}Vue$1.nextTick(function(){return oldVm.$destroy();});}}function installModule(store,rootState,path,module,hot){var isRoot=!path.length;var namespace=store._modules.getNamespace(path);// register in namespace map
if(module.namespaced){if(store._modulesNamespaceMap[namespace]&&"production"!=='production'){console.error("[vuex] duplicate namespace "+namespace+" for the namespaced module "+path.join('/'));}store._modulesNamespaceMap[namespace]=module;}// set state
if(!isRoot&&!hot){var parentState=getNestedState(rootState,path.slice(0,-1));var moduleName=path[path.length-1];store._withCommit(function(){Vue$1.set(parentState,moduleName,module.state);});}var local=module.context=makeLocalContext(store,namespace,path);module.forEachMutation(function(mutation,key){var namespacedType=namespace+key;registerMutation(store,namespacedType,mutation,local);});module.forEachAction(function(action,key){var type=action.root?key:namespace+key;var handler=action.handler||action;registerAction(store,type,handler,local);});module.forEachGetter(function(getter,key){var namespacedType=namespace+key;registerGetter(store,namespacedType,getter,local);});module.forEachChild(function(child,key){installModule(store,rootState,path.concat(key),child,hot);});}/**
   * make localized dispatch, commit, getters and state
   * if there is no namespace, just use root ones
   */function makeLocalContext(store,namespace,path){var noNamespace=namespace==='';var local={dispatch:noNamespace?store.dispatch:function(_type,_payload,_options){var args=unifyObjectStyle(_type,_payload,_options);var payload=args.payload;var options=args.options;var type=args.type;if(!options||!options.root){type=namespace+type;}return store.dispatch(type,payload);},commit:noNamespace?store.commit:function(_type,_payload,_options){var args=unifyObjectStyle(_type,_payload,_options);var payload=args.payload;var options=args.options;var type=args.type;if(!options||!options.root){type=namespace+type;}store.commit(type,payload,options);}};// getters and state object must be gotten lazily
// because they will be changed by vm update
Object.defineProperties(local,{getters:{get:noNamespace?function(){return store.getters;}:function(){return makeLocalGetters(store,namespace);}},state:{get:function get(){return getNestedState(store.state,path);}}});return local;}function makeLocalGetters(store,namespace){if(!store._makeLocalGettersCache[namespace]){var gettersProxy={};var splitPos=namespace.length;Object.keys(store.getters).forEach(function(type){// skip if the target getter is not match this namespace
if(type.slice(0,splitPos)!==namespace){return;}// extract local getter type
var localType=type.slice(splitPos);// Add a port to the getters proxy.
// Define as getter property because
// we do not want to evaluate the getters in this time.
Object.defineProperty(gettersProxy,localType,{get:function get(){return store.getters[type];},enumerable:true});});store._makeLocalGettersCache[namespace]=gettersProxy;}return store._makeLocalGettersCache[namespace];}function registerMutation(store,type,handler,local){var entry=store._mutations[type]||(store._mutations[type]=[]);entry.push(function wrappedMutationHandler(payload){handler.call(store,local.state,payload);});}function registerAction(store,type,handler,local){var entry=store._actions[type]||(store._actions[type]=[]);entry.push(function wrappedActionHandler(payload){var res=handler.call(store,{dispatch:local.dispatch,commit:local.commit,getters:local.getters,state:local.state,rootGetters:store.getters,rootState:store.state},payload);if(!isPromise$1(res)){res=Promise.resolve(res);}if(store._devtoolHook){return res["catch"](function(err){store._devtoolHook.emit('vuex:error',err);throw err;});}else{return res;}});}function registerGetter(store,type,rawGetter,local){if(store._wrappedGetters[type]){return;}store._wrappedGetters[type]=function wrappedGetter(store){return rawGetter(local.state,// local state
local.getters,// local getters
store.state,// root state
store.getters// root getters
);};}function enableStrictMode(store){store._vm.$watch(function(){return this._data.$$state;},function(){},{deep:true,sync:true});}function getNestedState(state,path){return path.length?path.reduce(function(state,key){return state[key];},state):state;}function unifyObjectStyle(type,payload,options){if(isObject$1(type)&&type.type){options=payload;payload=type;type=type.type;}return{type:type,payload:payload,options:options};}function install(_Vue){if(Vue$1&&_Vue===Vue$1){return;}Vue$1=_Vue;applyMixin(Vue$1);}/**
   * Reduce the code which written in Vue.js for getting the state.
   * @param {String} [namespace] - Module's namespace
   * @param {Object|Array} states # Object's item can be a function which accept state and getters for param, you can do something for state and getters in it.
   * @param {Object}
   */var mapState=normalizeNamespace(function(namespace,states){var res={};normalizeMap(states).forEach(function(ref){var key=ref.key;var val=ref.val;res[key]=function mappedState(){var state=this.$store.state;var getters=this.$store.getters;if(namespace){var module=getModuleByNamespace(this.$store,'mapState',namespace);if(!module){return;}state=module.context.state;getters=module.context.getters;}return typeof val==='function'?val.call(this,state,getters):state[val];};// mark vuex getter for devtools
res[key].vuex=true;});return res;});/**
   * Reduce the code which written in Vue.js for committing the mutation
   * @param {String} [namespace] - Module's namespace
   * @param {Object|Array} mutations # Object's item can be a function which accept `commit` function as the first param, it can accept anthor params. You can commit mutation and do any other things in this function. specially, You need to pass anthor params from the mapped function.
   * @return {Object}
   */var mapMutations=normalizeNamespace(function(namespace,mutations){var res={};normalizeMap(mutations).forEach(function(ref){var key=ref.key;var val=ref.val;res[key]=function mappedMutation(){var args=[],len=arguments.length;while(len--){args[len]=arguments[len];}// Get the commit method from store
var commit=this.$store.commit;if(namespace){var module=getModuleByNamespace(this.$store,'mapMutations',namespace);if(!module){return;}commit=module.context.commit;}return typeof val==='function'?val.apply(this,[commit].concat(args)):commit.apply(this.$store,[val].concat(args));};});return res;});/**
   * Reduce the code which written in Vue.js for getting the getters
   * @param {String} [namespace] - Module's namespace
   * @param {Object|Array} getters
   * @return {Object}
   */var mapGetters=normalizeNamespace(function(namespace,getters){var res={};normalizeMap(getters).forEach(function(ref){var key=ref.key;var val=ref.val;// The namespace has been mutated by normalizeNamespace
val=namespace+val;res[key]=function mappedGetter(){if(namespace&&!getModuleByNamespace(this.$store,'mapGetters',namespace)){return;}return this.$store.getters[val];};// mark vuex getter for devtools
res[key].vuex=true;});return res;});/**
   * Reduce the code which written in Vue.js for dispatch the action
   * @param {String} [namespace] - Module's namespace
   * @param {Object|Array} actions # Object's item can be a function which accept `dispatch` function as the first param, it can accept anthor params. You can dispatch action and do any other things in this function. specially, You need to pass anthor params from the mapped function.
   * @return {Object}
   */var mapActions=normalizeNamespace(function(namespace,actions){var res={};normalizeMap(actions).forEach(function(ref){var key=ref.key;var val=ref.val;res[key]=function mappedAction(){var args=[],len=arguments.length;while(len--){args[len]=arguments[len];}// get dispatch function from store
var dispatch=this.$store.dispatch;if(namespace){var module=getModuleByNamespace(this.$store,'mapActions',namespace);if(!module){return;}dispatch=module.context.dispatch;}return typeof val==='function'?val.apply(this,[dispatch].concat(args)):dispatch.apply(this.$store,[val].concat(args));};});return res;});/**
   * Rebinding namespace param for mapXXX function in special scoped, and return them by simple object
   * @param {String} namespace
   * @return {Object}
   */var createNamespacedHelpers=function createNamespacedHelpers(namespace){return{mapState:mapState.bind(null,namespace),mapGetters:mapGetters.bind(null,namespace),mapMutations:mapMutations.bind(null,namespace),mapActions:mapActions.bind(null,namespace)};};/**
   * Normalize the map
   * normalizeMap([1, 2, 3]) => [ { key: 1, val: 1 }, { key: 2, val: 2 }, { key: 3, val: 3 } ]
   * normalizeMap({a: 1, b: 2, c: 3}) => [ { key: 'a', val: 1 }, { key: 'b', val: 2 }, { key: 'c', val: 3 } ]
   * @param {Array|Object} map
   * @return {Object}
   */function normalizeMap(map){if(!isValidMap(map)){return[];}return Array.isArray(map)?map.map(function(key){return{key:key,val:key};}):Object.keys(map).map(function(key){return{key:key,val:map[key]};});}/**
   * Validate whether given map is valid or not
   * @param {*} map
   * @return {Boolean}
   */function isValidMap(map){return Array.isArray(map)||isObject$1(map);}/**
   * Return a function expect two param contains namespace and map. it will normalize the namespace and then the param's function will handle the new namespace and the map.
   * @param {Function} fn
   * @return {Function}
   */function normalizeNamespace(fn){return function(namespace,map){if(typeof namespace!=='string'){map=namespace;namespace='';}else if(namespace.charAt(namespace.length-1)!=='/'){namespace+='/';}return fn(namespace,map);};}/**
   * Search a special module from store by namespace. if module not exist, print error message.
   * @param {Object} store
   * @param {String} helper
   * @param {String} namespace
   * @return {Object}
   */function getModuleByNamespace(store,helper,namespace){var module=store._modulesNamespaceMap[namespace];return module;}var index_esm={Store:Store,install:install,version:'3.1.2',mapState:mapState,mapMutations:mapMutations,mapGetters:mapGetters,mapActions:mapActions,createNamespacedHelpers:createNamespacedHelpers};// Takes: https://developer.mozilla.org/en-US/docs/Web/API/DataTransferItem
// Returns: https://developer.mozilla.org/en-US/docs/Web/API/FileSystemFileEntry
var getFileEntryFromDataTransferItem=function getFileEntryFromDataTransferItem(file){if(typeof file.getAsEntry==='function'){return file.getAsEntry();}else if(typeof file.webkitGetAsEntry==='function'){return file.webkitGetAsEntry();}return undefined;};var isWantedFile=function isWantedFile(filename){var unwantedFiles=[// Stores thumbnails on OSX
'.DS_Store'];return unwantedFiles.indexOf(filename)===-1;};var getPath=function getPath(path,name){return"".concat(path,"/").concat(name);};var extractFromItems=function extractFromItems(items){var files=[];var traverseDirectoryTree=function traverseDirectoryTree(fileEntry){var path=arguments.length>1&&arguments[1]!==undefined?arguments[1]:'';var promises=[];return new Promise(function(resolve){if(fileEntry.isDirectory){var reader=fileEntry.createReader();var readFiles=function readFiles(){reader.readEntries(function(dirContent){dirContent.forEach(function(dirItem){promises.push(traverseDirectoryTree(dirItem,getPath(path,fileEntry.name)));});if(dirContent.length){readFiles();}else{Promise.all(promises).then(resolve);}});};readFiles();}else if(fileEntry.isFile){fileEntry.file(function(file){if(isWantedFile(file.name)){file.path=getPath(path,file.name);files.push(file);}resolve();});}});};var extractUrl=function extractUrl(item){return new Promise(function(resolve){item.getAsString(function(url){files.push({url:url,source:'dragged-from-web'});resolve();});});};var promises=[];for(var i=0;i<items.length;i+=1){var item=items[i];if(item.kind==='file'&&item.type&&item.type!=='application/x-moz-file'){var file=item.getAsFile();if(file){// It is a simple file
files.push(file);promises.push(Promise.resolve());}}else if(item.kind==='file'){// It's not a simple file, possibly folder, try to scout its content.
var _file=getFileEntryFromDataTransferItem(item);if(_file){promises.push(traverseDirectoryTree(_file));}}else if(item.kind==='string'&&item.type==='text/uri-list'){promises.push(extractUrl(item));}}return Promise.all(promises).then(function(){return files;});};var extractFromFiles=function extractFromFiles(fileList){return new Promise(function(resolve){var files=[];for(var i=0;i<fileList.length;i+=1){files.push(fileList[i]);}resolve(files);});};// Takses: https://developer.mozilla.org/en-US/docs/Web/API/DataTransfer
// Returns Array of possible file representations:
// 1. File class instance - https://developer.mozilla.org/en-US/docs/Web/API/File
// 2. Blob class instance - https://developer.mozilla.org/en-US/docs/Web/API/Blob
// 3. Object with url to resource - { url: 'https://files.com/file.jpg' }
var extractFilesFromDataTransfer=function extractFilesFromDataTransfer(dataTransfer){// if there is no dataTransfer object, just return empty promise
if(!dataTransfer){return Promise.resolve([]);}if(dataTransfer.items){return extractFromItems(dataTransfer.items);}if(dataTransfer.files){return extractFromFiles(dataTransfer.files);}// Safety fallback if this dataTransfer has nothing we can make sense of.
return Promise.resolve([]);};//
var script={data:function data(){return{fileAboutToBeDropped:false};},methods:_objectSpread({},index_esm.mapActions(['addFile','updateSelectLabelActive']),{dragenter:function dragenter(event){event.preventDefault();this.fileAboutToBeDropped=true;this.updateSelectLabelActive(true);},dragover:function dragover(event){event.preventDefault();},dragleave:function dragleave(){this.fileAboutToBeDropped=false;this.updateSelectLabelActive(false);},drop:function drop(event){var _this4=this;event.preventDefault();this.fileAboutToBeDropped=false;extractFilesFromDataTransfer(event.dataTransfer).then(function(files){files.forEach(function(file){_this4.addFile(file);});});},paste:function paste(event){var _this5=this;extractFilesFromDataTransfer(event.clipboardData).then(function(files){files.forEach(function(file){file.name='pasted file';_this5.addFile(file);});});}}),mounted:function mounted(){var root=this.$root.$el;var dropZone=this.$refs.dropZone;root.addEventListener('dragenter',this.dragenter,false);root.addEventListener('paste',this.paste,false);dropZone.addEventListener('dragover',this.dragover,false);dropZone.addEventListener('dragleave',this.dragleave,false);dropZone.addEventListener('drop',this.drop,false);},beforeDestroy:function beforeDestroy(){var root=this.$root.$el;root.removeEventListener('dragenter',this.dragenter);root.removeEventListener('paste',this.paste);}};function normalizeComponent(template,style,script,scopeId,isFunctionalTemplate,moduleIdentifier/* server only */,shadowMode,createInjector,createInjectorSSR,createInjectorShadow){if(typeof shadowMode!=='boolean'){createInjectorSSR=createInjector;createInjector=shadowMode;shadowMode=false;}// Vue.extend constructor export interop.
var options=typeof script==='function'?script.options:script;// render functions
if(template&&template.render){options.render=template.render;options.staticRenderFns=template.staticRenderFns;options._compiled=true;// functional template
if(isFunctionalTemplate){options.functional=true;}}// scopedId
if(scopeId){options._scopeId=scopeId;}var hook;if(moduleIdentifier){// server build
hook=function hook(context){// 2.3 injection
context=context||// cached call
this.$vnode&&this.$vnode.ssrContext||// stateful
this.parent&&this.parent.$vnode&&this.parent.$vnode.ssrContext;// functional
// 2.2 with runInNewContext: true
if(!context&&typeof __VUE_SSR_CONTEXT__!=='undefined'){context=__VUE_SSR_CONTEXT__;}// inject component styles
if(style){style.call(this,createInjectorSSR(context));}// register component module identifier for async chunk inference
if(context&&context._registeredComponents){context._registeredComponents.add(moduleIdentifier);}};// used by ssr in case component is cached and beforeCreate
// never gets called
options._ssrRegister=hook;}else if(style){hook=shadowMode?function(context){style.call(this,createInjectorShadow(context,this.$root.$options.shadowRoot));}:function(context){style.call(this,createInjector(context));};}if(hook){if(options.functional){// register for functional component in vue file
var originalRender=options.render;options.render=function renderWithStyleInjection(h,context){hook.call(context);return originalRender(h,context);};}else{// inject component registration as beforeCreate hook
var existing=options.beforeCreate;options.beforeCreate=existing?[].concat(existing,hook):[hook];}}return script;}var isOldIE=typeof navigator!=='undefined'&&/msie [6-9]\\b/.test(navigator.userAgent.toLowerCase());/* script */var __vue_script__=script;/* template */var __vue_render__=function __vue_render__(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{directives:[{name:"show",rawName:"v-show",value:_vm.fileAboutToBeDropped,expression:"fileAboutToBeDropped"}],ref:"dropZone",staticClass:"fsp-dropzone-overlay"},[_c("div",{staticClass:"fsp-dropzone-overlay__text"})]);};var __vue_staticRenderFns__=[];__vue_render__._withStripped=true;/* style */var __vue_inject_styles__=undefined;/* scoped */var __vue_scope_id__=undefined;/* module identifier */var __vue_module_identifier__=undefined;/* functional template */var __vue_is_functional_template__=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var DragAndDrop=normalizeComponent({render:__vue_render__,staticRenderFns:__vue_staticRenderFns__},__vue_inject_styles__,__vue_script__,__vue_scope_id__,__vue_is_functional_template__,__vue_module_identifier__,false,undefined,undefined,undefined);//
var script$1={computed:_objectSpread({},index_esm.mapGetters(['mobileNavActive'])),methods:_objectSpread({},index_esm.mapActions(['updateMobileNavActive']),{toggleNav:function toggleNav(){this.updateMobileNavActive(!this.mobileNavActive);}})};/* script */var __vue_script__$1=script$1;/* template */var __vue_render__$1=function __vue_render__$1(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-mobile-menu",on:{click:function click($event){return _vm.toggleNav();}}});};var __vue_staticRenderFns__$1=[];__vue_render__$1._withStripped=true;/* style */var __vue_inject_styles__$1=undefined;/* scoped */var __vue_scope_id__$1=undefined;/* module identifier */var __vue_module_identifier__$1=undefined;/* functional template */var __vue_is_functional_template__$1=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var MobileMenuButton=normalizeComponent({render:__vue_render__$1,staticRenderFns:__vue_staticRenderFns__$1},__vue_inject_styles__$1,__vue_script__$1,__vue_scope_id__$1,__vue_is_functional_template__$1,__vue_module_identifier__$1,false,undefined,undefined,undefined);//
var script$2={computed:_objectSpread({},index_esm.mapGetters(['cropFiles','customSourceName','fromSources','mobileNavActive']),{customSourceLabel:function customSourceLabel(){return this.customSourceName||'Custom Source';},isCustomSource:function isCustomSource(){return this.source.name==='customsource';}}),components:{MobileMenuButton:MobileMenuButton},props:['source','hideHeader','hideMenu']};/* script */var __vue_script__$2=script$2;/* template */var __vue_render__$2=function __vue_render__$2(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-header","class":{"fsp-header--hide":_vm.hideHeader}},[_vm.source&&!_vm.mobileNavActive?_c("span",{staticClass:"fsp-header-icon","class":"fsp-navbar--"+_vm.source.name,attrs:{title:_vm.t(_vm.source.label)}}):_vm._e(),_vm._v(" "),_vm.source&&_vm.isCustomSource&&!_vm.mobileNavActive?_c("span",{staticClass:"fsp-header-text--visible"},[_vm._v(" "+_vm._s(_vm.t(_vm.customSourceLabel))+" ")]):_vm._e(),_vm._v(" "),_vm.mobileNavActive?_c("span",{staticClass:"fsp-header-text"},[_vm._v(" "+_vm._s(_vm.t("Select From"))+" ")]):_vm._e(),_vm._v(" "),!_vm.mobileNavActive?_vm._t("default"):_vm._e(),_vm._v(" "),!_vm.hideMenu&&!_vm.cropFiles?_c("mobile-menu-button"):_vm._e()],2);};var __vue_staticRenderFns__$2=[];__vue_render__$2._withStripped=true;/* style */var __vue_inject_styles__$2=undefined;/* scoped */var __vue_scope_id__$2=undefined;/* module identifier */var __vue_module_identifier__$2=undefined;/* functional template */var __vue_is_functional_template__$2=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var ContentHeader=normalizeComponent({render:__vue_render__$2,staticRenderFns:__vue_staticRenderFns__$2},__vue_inject_styles__$2,__vue_script__$2,__vue_scope_id__$2,__vue_is_functional_template__$2,__vue_module_identifier__$2,false,undefined,undefined,undefined);/**
   * lodash (Custom Build) <https://lodash.com/>
   * Build: `lodash modularize exports="npm" -o ./`
   * Copyright jQuery Foundation and other contributors <https://jquery.org/>
   * Released under MIT license <https://lodash.com/license>
   * Based on Underscore.js 1.8.3 <http://underscorejs.org/LICENSE>
   * Copyright Jeremy Ashkenas, DocumentCloud and Investigative Reporters & Editors
   */ /** Used as the `TypeError` message for "Functions" methods. */var FUNC_ERROR_TEXT='Expected a function';/** Used as references for various `Number` constants. */var NAN=0/0;/** `Object#toString` result references. */var symbolTag$1='[object Symbol]';/** Used to match leading and trailing whitespace. */var reTrim=/^\s+|\s+$/g;/** Used to detect bad signed hexadecimal string values. */var reIsBadHex=/^[-+]0x[0-9a-f]+$/i;/** Used to detect binary string values. */var reIsBinary=/^0b[01]+$/i;/** Used to detect octal string values. */var reIsOctal=/^0o[0-7]+$/i;/** Built-in method references without a dependency on `root`. */var freeParseInt=parseInt;/** Detect free variable `global` from Node.js. */var freeGlobal$1=_typeof2(commonjsGlobal)=='object'&&commonjsGlobal&&commonjsGlobal.Object===Object&&commonjsGlobal;/** Detect free variable `self`. */var freeSelf$1=(typeof self==="undefined"?"undefined":_typeof2(self))=='object'&&self&&self.Object===Object&&self;/** Used as a reference to the global object. */var root$1=freeGlobal$1||freeSelf$1||Function('return this')();/** Used for built-in method references. */var objectProto$1=Object.prototype;/**
   * Used to resolve the
   * [`toStringTag`](http://ecma-international.org/ecma-262/7.0/#sec-object.prototype.tostring)
   * of values.
   */var objectToString$1=objectProto$1.toString;/* Built-in method references for those with the same name as other `lodash` methods. */var nativeMax=Math.max,nativeMin=Math.min;/**
   * Gets the timestamp of the number of milliseconds that have elapsed since
   * the Unix epoch (1 January 1970 00:00:00 UTC).
   *
   * @static
   * @memberOf _
   * @since 2.4.0
   * @category Date
   * @returns {number} Returns the timestamp.
   * @example
   *
   * _.defer(function(stamp) {
   *   console.log(_.now() - stamp);
   * }, _.now());
   * // => Logs the number of milliseconds it took for the deferred invocation.
   */var now=function now(){return root$1.Date.now();};/**
   * Creates a debounced function that delays invoking `func` until after `wait`
   * milliseconds have elapsed since the last time the debounced function was
   * invoked. The debounced function comes with a `cancel` method to cancel
   * delayed `func` invocations and a `flush` method to immediately invoke them.
   * Provide `options` to indicate whether `func` should be invoked on the
   * leading and/or trailing edge of the `wait` timeout. The `func` is invoked
   * with the last arguments provided to the debounced function. Subsequent
   * calls to the debounced function return the result of the last `func`
   * invocation.
   *
   * **Note:** If `leading` and `trailing` options are `true`, `func` is
   * invoked on the trailing edge of the timeout only if the debounced function
   * is invoked more than once during the `wait` timeout.
   *
   * If `wait` is `0` and `leading` is `false`, `func` invocation is deferred
   * until to the next tick, similar to `setTimeout` with a timeout of `0`.
   *
   * See [David Corbacho's article](https://css-tricks.com/debouncing-throttling-explained-examples/)
   * for details over the differences between `_.debounce` and `_.throttle`.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Function
   * @param {Function} func The function to debounce.
   * @param {number} [wait=0] The number of milliseconds to delay.
   * @param {Object} [options={}] The options object.
   * @param {boolean} [options.leading=false]
   *  Specify invoking on the leading edge of the timeout.
   * @param {number} [options.maxWait]
   *  The maximum time `func` is allowed to be delayed before it's invoked.
   * @param {boolean} [options.trailing=true]
   *  Specify invoking on the trailing edge of the timeout.
   * @returns {Function} Returns the new debounced function.
   * @example
   *
   * // Avoid costly calculations while the window size is in flux.
   * jQuery(window).on('resize', _.debounce(calculateLayout, 150));
   *
   * // Invoke `sendMail` when clicked, debouncing subsequent calls.
   * jQuery(element).on('click', _.debounce(sendMail, 300, {
   *   'leading': true,
   *   'trailing': false
   * }));
   *
   * // Ensure `batchLog` is invoked once after 1 second of debounced calls.
   * var debounced = _.debounce(batchLog, 250, { 'maxWait': 1000 });
   * var source = new EventSource('/stream');
   * jQuery(source).on('message', debounced);
   *
   * // Cancel the trailing debounced invocation.
   * jQuery(window).on('popstate', debounced.cancel);
   */function debounce(func,wait,options){var lastArgs,lastThis,maxWait,result,timerId,lastCallTime,lastInvokeTime=0,leading=false,maxing=false,trailing=true;if(typeof func!='function'){throw new TypeError(FUNC_ERROR_TEXT);}wait=toNumber$1(wait)||0;if(isObject$2(options)){leading=!!options.leading;maxing='maxWait'in options;maxWait=maxing?nativeMax(toNumber$1(options.maxWait)||0,wait):maxWait;trailing='trailing'in options?!!options.trailing:trailing;}function invokeFunc(time){var args=lastArgs,thisArg=lastThis;lastArgs=lastThis=undefined;lastInvokeTime=time;result=func.apply(thisArg,args);return result;}function leadingEdge(time){// Reset any `maxWait` timer.
lastInvokeTime=time;// Start the timer for the trailing edge.
timerId=setTimeout(timerExpired,wait);// Invoke the leading edge.
return leading?invokeFunc(time):result;}function remainingWait(time){var timeSinceLastCall=time-lastCallTime,timeSinceLastInvoke=time-lastInvokeTime,result=wait-timeSinceLastCall;return maxing?nativeMin(result,maxWait-timeSinceLastInvoke):result;}function shouldInvoke(time){var timeSinceLastCall=time-lastCallTime,timeSinceLastInvoke=time-lastInvokeTime;// Either this is the first call, activity has stopped and we're at the
// trailing edge, the system time has gone backwards and we're treating
// it as the trailing edge, or we've hit the `maxWait` limit.
return lastCallTime===undefined||timeSinceLastCall>=wait||timeSinceLastCall<0||maxing&&timeSinceLastInvoke>=maxWait;}function timerExpired(){var time=now();if(shouldInvoke(time)){return trailingEdge(time);}// Restart the timer.
timerId=setTimeout(timerExpired,remainingWait(time));}function trailingEdge(time){timerId=undefined;// Only invoke if we have `lastArgs` which means `func` has been
// debounced at least once.
if(trailing&&lastArgs){return invokeFunc(time);}lastArgs=lastThis=undefined;return result;}function cancel(){if(timerId!==undefined){clearTimeout(timerId);}lastInvokeTime=0;lastArgs=lastCallTime=lastThis=timerId=undefined;}function flush(){return timerId===undefined?result:trailingEdge(now());}function debounced(){var time=now(),isInvoking=shouldInvoke(time);lastArgs=arguments;lastThis=this;lastCallTime=time;if(isInvoking){if(timerId===undefined){return leadingEdge(lastCallTime);}if(maxing){// Handle invocations in a tight loop.
timerId=setTimeout(timerExpired,wait);return invokeFunc(lastCallTime);}}if(timerId===undefined){timerId=setTimeout(timerExpired,wait);}return result;}debounced.cancel=cancel;debounced.flush=flush;return debounced;}/**
   * Creates a throttled function that only invokes `func` at most once per
   * every `wait` milliseconds. The throttled function comes with a `cancel`
   * method to cancel delayed `func` invocations and a `flush` method to
   * immediately invoke them. Provide `options` to indicate whether `func`
   * should be invoked on the leading and/or trailing edge of the `wait`
   * timeout. The `func` is invoked with the last arguments provided to the
   * throttled function. Subsequent calls to the throttled function return the
   * result of the last `func` invocation.
   *
   * **Note:** If `leading` and `trailing` options are `true`, `func` is
   * invoked on the trailing edge of the timeout only if the throttled function
   * is invoked more than once during the `wait` timeout.
   *
   * If `wait` is `0` and `leading` is `false`, `func` invocation is deferred
   * until to the next tick, similar to `setTimeout` with a timeout of `0`.
   *
   * See [David Corbacho's article](https://css-tricks.com/debouncing-throttling-explained-examples/)
   * for details over the differences between `_.throttle` and `_.debounce`.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Function
   * @param {Function} func The function to throttle.
   * @param {number} [wait=0] The number of milliseconds to throttle invocations to.
   * @param {Object} [options={}] The options object.
   * @param {boolean} [options.leading=true]
   *  Specify invoking on the leading edge of the timeout.
   * @param {boolean} [options.trailing=true]
   *  Specify invoking on the trailing edge of the timeout.
   * @returns {Function} Returns the new throttled function.
   * @example
   *
   * // Avoid excessively updating the position while scrolling.
   * jQuery(window).on('scroll', _.throttle(updatePosition, 100));
   *
   * // Invoke `renewToken` when the click event is fired, but not more than once every 5 minutes.
   * var throttled = _.throttle(renewToken, 300000, { 'trailing': false });
   * jQuery(element).on('click', throttled);
   *
   * // Cancel the trailing throttled invocation.
   * jQuery(window).on('popstate', throttled.cancel);
   */function throttle(func,wait,options){var leading=true,trailing=true;if(typeof func!='function'){throw new TypeError(FUNC_ERROR_TEXT);}if(isObject$2(options)){leading='leading'in options?!!options.leading:leading;trailing='trailing'in options?!!options.trailing:trailing;}return debounce(func,wait,{'leading':leading,'maxWait':wait,'trailing':trailing});}/**
   * Checks if `value` is the
   * [language type](http://www.ecma-international.org/ecma-262/7.0/#sec-ecmascript-language-types)
   * of `Object`. (e.g. arrays, functions, objects, regexes, `new Number(0)`, and `new String('')`)
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an object, else `false`.
   * @example
   *
   * _.isObject({});
   * // => true
   *
   * _.isObject([1, 2, 3]);
   * // => true
   *
   * _.isObject(_.noop);
   * // => true
   *
   * _.isObject(null);
   * // => false
   */function isObject$2(value){var type=_typeof2(value);return!!value&&(type=='object'||type=='function');}/**
   * Checks if `value` is object-like. A value is object-like if it's not `null`
   * and has a `typeof` result of "object".
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is object-like, else `false`.
   * @example
   *
   * _.isObjectLike({});
   * // => true
   *
   * _.isObjectLike([1, 2, 3]);
   * // => true
   *
   * _.isObjectLike(_.noop);
   * // => false
   *
   * _.isObjectLike(null);
   * // => false
   */function isObjectLike$1(value){return!!value&&_typeof2(value)=='object';}/**
   * Checks if `value` is classified as a `Symbol` primitive or object.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a symbol, else `false`.
   * @example
   *
   * _.isSymbol(Symbol.iterator);
   * // => true
   *
   * _.isSymbol('abc');
   * // => false
   */function isSymbol$1(value){return _typeof2(value)=='symbol'||isObjectLike$1(value)&&objectToString$1.call(value)==symbolTag$1;}/**
   * Converts `value` to a number.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to process.
   * @returns {number} Returns the number.
   * @example
   *
   * _.toNumber(3.2);
   * // => 3.2
   *
   * _.toNumber(Number.MIN_VALUE);
   * // => 5e-324
   *
   * _.toNumber(Infinity);
   * // => Infinity
   *
   * _.toNumber('3.2');
   * // => 3.2
   */function toNumber$1(value){if(typeof value=='number'){return value;}if(isSymbol$1(value)){return NAN;}if(isObject$2(value)){var other=typeof value.valueOf=='function'?value.valueOf():value;value=isObject$2(other)?other+'':other;}if(typeof value!='string'){return value===0?value:+value;}value=value.replace(reTrim,'');var isBinary=reIsBinary.test(value);return isBinary||reIsOctal.test(value)?freeParseInt(value.slice(2),isBinary?2:8):reIsBadHex.test(value)?NAN:+value;}var lodash_throttle=throttle;var bowser=createCommonjsModule(function(module){/*!
   * Bowser - a browser detector
   * https://github.com/ded/bowser
   * MIT License | (c) Dustin Diaz 2015
   */!function(root,name,definition){if(module.exports)module.exports=definition();else root[name]=definition();}(commonjsGlobal,'bowser',function(){/**
      * See useragents.js for examples of navigator.userAgent
      */var t=true;function detect(ua){function getFirstMatch(regex){var match=ua.match(regex);return match&&match.length>1&&match[1]||'';}function getSecondMatch(regex){var match=ua.match(regex);return match&&match.length>1&&match[2]||'';}var iosdevice=getFirstMatch(/(ipod|iphone|ipad)/i).toLowerCase(),likeAndroid=/like android/i.test(ua),android=!likeAndroid&&/android/i.test(ua),nexusMobile=/nexus\s*[0-6]\s*/i.test(ua),nexusTablet=!nexusMobile&&/nexus\s*[0-9]+/i.test(ua),chromeos=/CrOS/.test(ua),silk=/silk/i.test(ua),sailfish=/sailfish/i.test(ua),tizen=/tizen/i.test(ua),webos=/(web|hpw)(o|0)s/i.test(ua),windowsphone=/windows phone/i.test(ua),samsungBrowser=/SamsungBrowser/i.test(ua),windows=!windowsphone&&/windows/i.test(ua),mac=!iosdevice&&!silk&&/macintosh/i.test(ua),linux=!android&&!sailfish&&!tizen&&!webos&&/linux/i.test(ua),edgeVersion=getSecondMatch(/edg([ea]|ios)\/(\d+(\.\d+)?)/i),versionIdentifier=getFirstMatch(/version\/(\d+(\.\d+)?)/i),tablet=/tablet/i.test(ua)&&!/tablet pc/i.test(ua),mobile=!tablet&&/[^-]mobi/i.test(ua),xbox=/xbox/i.test(ua),result;if(/opera/i.test(ua)){//  an old Opera
result={name:'Opera',opera:t,version:versionIdentifier||getFirstMatch(/(?:opera|opr|opios)[\s\/](\d+(\.\d+)?)/i)};}else if(/opr\/|opios/i.test(ua)){// a new Opera
result={name:'Opera',opera:t,version:getFirstMatch(/(?:opr|opios)[\s\/](\d+(\.\d+)?)/i)||versionIdentifier};}else if(/SamsungBrowser/i.test(ua)){result={name:'Samsung Internet for Android',samsungBrowser:t,version:versionIdentifier||getFirstMatch(/(?:SamsungBrowser)[\s\/](\d+(\.\d+)?)/i)};}else if(/Whale/i.test(ua)){result={name:'NAVER Whale browser',whale:t,version:getFirstMatch(/(?:whale)[\s\/](\d+(?:\.\d+)+)/i)};}else if(/MZBrowser/i.test(ua)){result={name:'MZ Browser',mzbrowser:t,version:getFirstMatch(/(?:MZBrowser)[\s\/](\d+(?:\.\d+)+)/i)};}else if(/coast/i.test(ua)){result={name:'Opera Coast',coast:t,version:versionIdentifier||getFirstMatch(/(?:coast)[\s\/](\d+(\.\d+)?)/i)};}else if(/focus/i.test(ua)){result={name:'Focus',focus:t,version:getFirstMatch(/(?:focus)[\s\/](\d+(?:\.\d+)+)/i)};}else if(/yabrowser/i.test(ua)){result={name:'Yandex Browser',yandexbrowser:t,version:versionIdentifier||getFirstMatch(/(?:yabrowser)[\s\/](\d+(\.\d+)?)/i)};}else if(/ucbrowser/i.test(ua)){result={name:'UC Browser',ucbrowser:t,version:getFirstMatch(/(?:ucbrowser)[\s\/](\d+(?:\.\d+)+)/i)};}else if(/mxios/i.test(ua)){result={name:'Maxthon',maxthon:t,version:getFirstMatch(/(?:mxios)[\s\/](\d+(?:\.\d+)+)/i)};}else if(/epiphany/i.test(ua)){result={name:'Epiphany',epiphany:t,version:getFirstMatch(/(?:epiphany)[\s\/](\d+(?:\.\d+)+)/i)};}else if(/puffin/i.test(ua)){result={name:'Puffin',puffin:t,version:getFirstMatch(/(?:puffin)[\s\/](\d+(?:\.\d+)?)/i)};}else if(/sleipnir/i.test(ua)){result={name:'Sleipnir',sleipnir:t,version:getFirstMatch(/(?:sleipnir)[\s\/](\d+(?:\.\d+)+)/i)};}else if(/k-meleon/i.test(ua)){result={name:'K-Meleon',kMeleon:t,version:getFirstMatch(/(?:k-meleon)[\s\/](\d+(?:\.\d+)+)/i)};}else if(windowsphone){result={name:'Windows Phone',osname:'Windows Phone',windowsphone:t};if(edgeVersion){result.msedge=t;result.version=edgeVersion;}else{result.msie=t;result.version=getFirstMatch(/iemobile\/(\d+(\.\d+)?)/i);}}else if(/msie|trident/i.test(ua)){result={name:'Internet Explorer',msie:t,version:getFirstMatch(/(?:msie |rv:)(\d+(\.\d+)?)/i)};}else if(chromeos){result={name:'Chrome',osname:'Chrome OS',chromeos:t,chromeBook:t,chrome:t,version:getFirstMatch(/(?:chrome|crios|crmo)\/(\d+(\.\d+)?)/i)};}else if(/edg([ea]|ios)/i.test(ua)){result={name:'Microsoft Edge',msedge:t,version:edgeVersion};}else if(/vivaldi/i.test(ua)){result={name:'Vivaldi',vivaldi:t,version:getFirstMatch(/vivaldi\/(\d+(\.\d+)?)/i)||versionIdentifier};}else if(sailfish){result={name:'Sailfish',osname:'Sailfish OS',sailfish:t,version:getFirstMatch(/sailfish\s?browser\/(\d+(\.\d+)?)/i)};}else if(/seamonkey\//i.test(ua)){result={name:'SeaMonkey',seamonkey:t,version:getFirstMatch(/seamonkey\/(\d+(\.\d+)?)/i)};}else if(/firefox|iceweasel|fxios/i.test(ua)){result={name:'Firefox',firefox:t,version:getFirstMatch(/(?:firefox|iceweasel|fxios)[ \/](\d+(\.\d+)?)/i)};if(/\((mobile|tablet);[^\)]*rv:[\d\.]+\)/i.test(ua)){result.firefoxos=t;result.osname='Firefox OS';}}else if(silk){result={name:'Amazon Silk',silk:t,version:getFirstMatch(/silk\/(\d+(\.\d+)?)/i)};}else if(/phantom/i.test(ua)){result={name:'PhantomJS',phantom:t,version:getFirstMatch(/phantomjs\/(\d+(\.\d+)?)/i)};}else if(/slimerjs/i.test(ua)){result={name:'SlimerJS',slimer:t,version:getFirstMatch(/slimerjs\/(\d+(\.\d+)?)/i)};}else if(/blackberry|\bbb\d+/i.test(ua)||/rim\stablet/i.test(ua)){result={name:'BlackBerry',osname:'BlackBerry OS',blackberry:t,version:versionIdentifier||getFirstMatch(/blackberry[\d]+\/(\d+(\.\d+)?)/i)};}else if(webos){result={name:'WebOS',osname:'WebOS',webos:t,version:versionIdentifier||getFirstMatch(/w(?:eb)?osbrowser\/(\d+(\.\d+)?)/i)};/touchpad\//i.test(ua)&&(result.touchpad=t);}else if(/bada/i.test(ua)){result={name:'Bada',osname:'Bada',bada:t,version:getFirstMatch(/dolfin\/(\d+(\.\d+)?)/i)};}else if(tizen){result={name:'Tizen',osname:'Tizen',tizen:t,version:getFirstMatch(/(?:tizen\s?)?browser\/(\d+(\.\d+)?)/i)||versionIdentifier};}else if(/qupzilla/i.test(ua)){result={name:'QupZilla',qupzilla:t,version:getFirstMatch(/(?:qupzilla)[\s\/](\d+(?:\.\d+)+)/i)||versionIdentifier};}else if(/chromium/i.test(ua)){result={name:'Chromium',chromium:t,version:getFirstMatch(/(?:chromium)[\s\/](\d+(?:\.\d+)?)/i)||versionIdentifier};}else if(/chrome|crios|crmo/i.test(ua)){result={name:'Chrome',chrome:t,version:getFirstMatch(/(?:chrome|crios|crmo)\/(\d+(\.\d+)?)/i)};}else if(android){result={name:'Android',version:versionIdentifier};}else if(/safari|applewebkit/i.test(ua)){result={name:'Safari',safari:t};if(versionIdentifier){result.version=versionIdentifier;}}else if(iosdevice){result={name:iosdevice=='iphone'?'iPhone':iosdevice=='ipad'?'iPad':'iPod'};// WTF: version is not part of user agent in web apps
if(versionIdentifier){result.version=versionIdentifier;}}else if(/googlebot/i.test(ua)){result={name:'Googlebot',googlebot:t,version:getFirstMatch(/googlebot\/(\d+(\.\d+))/i)||versionIdentifier};}else{result={name:getFirstMatch(/^(.*)\/(.*) /),version:getSecondMatch(/^(.*)\/(.*) /)};}// set webkit or gecko flag for browsers based on these engines
if(!result.msedge&&/(apple)?webkit/i.test(ua)){if(/(apple)?webkit\/537\.36/i.test(ua)){result.name=result.name||"Blink";result.blink=t;}else{result.name=result.name||"Webkit";result.webkit=t;}if(!result.version&&versionIdentifier){result.version=versionIdentifier;}}else if(!result.opera&&/gecko\//i.test(ua)){result.name=result.name||"Gecko";result.gecko=t;result.version=result.version||getFirstMatch(/gecko\/(\d+(\.\d+)?)/i);}// set OS flags for platforms that have multiple browsers
if(!result.windowsphone&&(android||result.silk)){result.android=t;result.osname='Android';}else if(!result.windowsphone&&iosdevice){result[iosdevice]=t;result.ios=t;result.osname='iOS';}else if(mac){result.mac=t;result.osname='macOS';}else if(xbox){result.xbox=t;result.osname='Xbox';}else if(windows){result.windows=t;result.osname='Windows';}else if(linux){result.linux=t;result.osname='Linux';}function getWindowsVersion(s){switch(s){case'NT':return'NT';case'XP':return'XP';case'NT 5.0':return'2000';case'NT 5.1':return'XP';case'NT 5.2':return'2003';case'NT 6.0':return'Vista';case'NT 6.1':return'7';case'NT 6.2':return'8';case'NT 6.3':return'8.1';case'NT 10.0':return'10';default:return undefined;}}// OS version extraction
var osVersion='';if(result.windows){osVersion=getWindowsVersion(getFirstMatch(/Windows ((NT|XP)( \d\d?.\d)?)/i));}else if(result.windowsphone){osVersion=getFirstMatch(/windows phone (?:os)?\s?(\d+(\.\d+)*)/i);}else if(result.mac){osVersion=getFirstMatch(/Mac OS X (\d+([_\.\s]\d+)*)/i);osVersion=osVersion.replace(/[_\s]/g,'.');}else if(iosdevice){osVersion=getFirstMatch(/os (\d+([_\s]\d+)*) like mac os x/i);osVersion=osVersion.replace(/[_\s]/g,'.');}else if(android){osVersion=getFirstMatch(/android[ \/-](\d+(\.\d+)*)/i);}else if(result.webos){osVersion=getFirstMatch(/(?:web|hpw)os\/(\d+(\.\d+)*)/i);}else if(result.blackberry){osVersion=getFirstMatch(/rim\stablet\sos\s(\d+(\.\d+)*)/i);}else if(result.bada){osVersion=getFirstMatch(/bada\/(\d+(\.\d+)*)/i);}else if(result.tizen){osVersion=getFirstMatch(/tizen[\/\s](\d+(\.\d+)*)/i);}if(osVersion){result.osversion=osVersion;}// device type extraction
var osMajorVersion=!result.windows&&osVersion.split('.')[0];if(tablet||nexusTablet||iosdevice=='ipad'||android&&(osMajorVersion==3||osMajorVersion>=4&&!mobile)||result.silk){result.tablet=t;}else if(mobile||iosdevice=='iphone'||iosdevice=='ipod'||android||nexusMobile||result.blackberry||result.webos||result.bada){result.mobile=t;}// Graded Browser Support
// http://developer.yahoo.com/yui/articles/gbs
if(result.msedge||result.msie&&result.version>=10||result.yandexbrowser&&result.version>=15||result.vivaldi&&result.version>=1.0||result.chrome&&result.version>=20||result.samsungBrowser&&result.version>=4||result.whale&&compareVersions([result.version,'1.0'])===1||result.mzbrowser&&compareVersions([result.version,'6.0'])===1||result.focus&&compareVersions([result.version,'1.0'])===1||result.firefox&&result.version>=20.0||result.safari&&result.version>=6||result.opera&&result.version>=10.0||result.ios&&result.osversion&&result.osversion.split(".")[0]>=6||result.blackberry&&result.version>=10.1||result.chromium&&result.version>=20){result.a=t;}else if(result.msie&&result.version<10||result.chrome&&result.version<20||result.firefox&&result.version<20.0||result.safari&&result.version<6||result.opera&&result.version<10.0||result.ios&&result.osversion&&result.osversion.split(".")[0]<6||result.chromium&&result.version<20){result.c=t;}else result.x=t;return result;}var bowser=detect(typeof navigator!=='undefined'?navigator.userAgent||'':'');bowser.test=function(browserList){for(var i=0;i<browserList.length;++i){var browserItem=browserList[i];if(typeof browserItem==='string'){if(browserItem in bowser){return true;}}}return false;};/**
     * Get version precisions count
     *
     * @example
     *   getVersionPrecision("1.10.3") // 3
     *
     * @param  {string} version
     * @return {number}
     */function getVersionPrecision(version){return version.split(".").length;}/**
     * Array::map polyfill
     *
     * @param  {Array} arr
     * @param  {Function} iterator
     * @return {Array}
     */function map(arr,iterator){var result=[],i;if(Array.prototype.map){return Array.prototype.map.call(arr,iterator);}for(i=0;i<arr.length;i++){result.push(iterator(arr[i]));}return result;}/**
     * Calculate browser version weight
     *
     * @example
     *   compareVersions(['1.10.2.1',  '1.8.2.1.90'])    // 1
     *   compareVersions(['1.010.2.1', '1.09.2.1.90']);  // 1
     *   compareVersions(['1.10.2.1',  '1.10.2.1']);     // 0
     *   compareVersions(['1.10.2.1',  '1.0800.2']);     // -1
     *
     * @param  {Array<String>} versions versions to compare
     * @return {Number} comparison result
     */function compareVersions(versions){// 1) get common precision for both versions, for example for "10.0" and "9" it should be 2
var precision=Math.max(getVersionPrecision(versions[0]),getVersionPrecision(versions[1]));var chunks=map(versions,function(version){var delta=precision-getVersionPrecision(version);// 2) "9" -> "9.0" (for precision = 2)
version=version+new Array(delta+1).join(".0");// 3) "9.0" -> ["000000000"", "000000009"]
return map(version.split("."),function(chunk){return new Array(20-chunk.length).join("0")+chunk;}).reverse();});// iterate in reverse order by reversed chunks array
while(--precision>=0){// 4) compare: "000000009" > "000000010" = false (but "9" > "10" = true)
if(chunks[0][precision]>chunks[1][precision]){return 1;}else if(chunks[0][precision]===chunks[1][precision]){if(precision===0){// all version chunks are same
return 0;}}else{return-1;}}}/**
     * Check if browser is unsupported
     *
     * @example
     *   bowser.isUnsupportedBrowser({
     *     msie: "10",
     *     firefox: "23",
     *     chrome: "29",
     *     safari: "5.1",
     *     opera: "16",
     *     phantom: "534"
     *   });
     *
     * @param  {Object}  minVersions map of minimal version to browser
     * @param  {Boolean} [strictMode = false] flag to return false if browser wasn't found in map
     * @param  {String}  [ua] user agent string
     * @return {Boolean}
     */function isUnsupportedBrowser(minVersions,strictMode,ua){var _bowser=bowser;// make strictMode param optional with ua param usage
if(typeof strictMode==='string'){ua=strictMode;strictMode=void 0;}if(strictMode===void 0){strictMode=false;}if(ua){_bowser=detect(ua);}var version=""+_bowser.version;for(var browser in minVersions){if(minVersions.hasOwnProperty(browser)){if(_bowser[browser]){if(typeof minVersions[browser]!=='string'){throw new Error('Browser version in the minVersion map should be a string: '+browser+': '+String(minVersions));}// browser version and min supported version.
return compareVersions([version,minVersions[browser]])<0;}}}return strictMode;// not found
}/**
     * Check if browser is supported
     *
     * @param  {Object} minVersions map of minimal version to browser
     * @param  {Boolean} [strictMode = false] flag to return false if browser wasn't found in map
     * @param  {String}  [ua] user agent string
     * @return {Boolean}
     */function check(minVersions,strictMode,ua){return!isUnsupportedBrowser(minVersions,strictMode,ua);}bowser.isUnsupportedBrowser=isUnsupportedBrowser;bowser.compareVersions=compareVersions;bowser.check=check;/*
     * Set our detect method to the main bowser object so we can
     * reuse it to test other user agents.
     * This is needed to implement future tests.
     */bowser._detect=detect;/*
     * Set our detect public method to the main bowser object
     * This is needed to implement bowser in server side
     */bowser.detect=detect;return bowser;});});//
var script$3={props:['sourceName','sourceLabel'],computed:_objectSpread({},index_esm.mapGetters(['accept','canAddMoreFiles','clouds','customSourceName','filesWaiting','route','maxFiles','mobileNavActive','uploadStarted']),{acceptStr:function acceptStr(){if(this.accept){return this.accept.join(',');}return undefined;},itemClasses:function itemClasses(){return{'fsp-source-list__item':true,'fsp-source-list__item--active':this.isSelectedSource,'fsp-source-list__item--disabled':this.uploadStarted};},itemLabel:function itemLabel(){if(this.sourceName==='customsource'){return this.customSourceName||'Custom Source';}return this.sourceLabel;},isSelectedSource:function isSelectedSource(){if(this.route[0]==='summary'){return false;}var selectedRoute=this.route.length>1?this.route[1]:'local_file_system';return selectedRoute===this.sourceName;},isAuthorized:function isAuthorized(){// Custom source doesn't require auth
if(this.sourceName==='customsource'){return false;}var cloud=this.clouds[this.sourceName];return cloud&&cloud.status==='ready';},isMobileLocal:function isMobileLocal(){if(bowser.mobile&&this.mobileNavActive){return this.sourceName==='local_file_system'||this.sourceName==='video'||this.sourceName==='audio'||this.sourceName==='webcam';}return false;},multiple:function multiple(){return this.maxFiles>1;},sourceSelectedCount:function sourceSelectedCount(){var _this6=this;var checkSelected=this.filesWaiting.filter(function(fw){return fw.source===_this6.sourceName;});return checkSelected.length;}}),methods:_objectSpread({},index_esm.mapActions(['updateMobileNavActive','addFile','logout']),{clearEvent:function clearEvent(event){event.target.value=null;},onNavClick:function onNavClick(sourceName){if(this.isMobileLocal){this.openSelectFile();}else{this.updateMobileNavActive(false);this.$store.dispatch('goToLastPath',sourceName);}},openSelectFile:function openSelectFile(){this.$refs.mobileLocaInput.click();},onFilesSelected:function onFilesSelected(event){var files=event.target.files;for(var i=0;i<files.length;i+=1){this.addFile(files[i]);}}})};/* script */var __vue_script__$3=script$3;/* template */var __vue_render__$3=function __vue_render__$3(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{"class":_vm.itemClasses,attrs:{title:_vm.t(_vm.itemLabel),tabindex:"0"},on:{keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.onNavClick(_vm.sourceName);},click:function click($event){return _vm.onNavClick(_vm.sourceName);}}},[_vm.sourceSelectedCount?_c("span",{staticClass:"fsp-badge--source"},[_vm._v(_vm._s(_vm.sourceSelectedCount))]):_vm._e(),_vm._v(" "),_c("span",{staticClass:"fsp-source-list__icon fsp-icon","class":"fsp-icon--"+_vm.sourceName}),_vm._v(" "),_c("span",{staticClass:"fsp-source-list__label"},[_vm._v(_vm._s(_vm.t(_vm.itemLabel)))]),_vm._v(" "),_vm.isAuthorized?_c("span",{staticClass:"fsp-source-list__logout",on:{click:function click($event){$event.stopPropagation();return _vm.logout(_vm.sourceName);}}},[_vm._v(" "+_vm._s(_vm.t("Sign Out")))]):_vm._e(),_vm._v(" "),_vm.isMobileLocal?_c("input",{ref:"mobileLocaInput",staticClass:"fsp-local-source__fileinput",attrs:{type:"file",accept:_vm.acceptStr,multiple:_vm.multiple,disabled:!_vm.canAddMoreFiles},on:{change:function change($event){return _vm.onFilesSelected($event);},click:function click($event){return _vm.clearEvent($event);}}}):_vm._e()]);};var __vue_staticRenderFns__$3=[];__vue_render__$3._withStripped=true;/* style */var __vue_inject_styles__$3=undefined;/* scoped */var __vue_scope_id__$3=undefined;/* module identifier */var __vue_module_identifier__$3=undefined;/* functional template */var __vue_is_functional_template__$3=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var SourceNavItem=normalizeComponent({render:__vue_render__$3,staticRenderFns:__vue_staticRenderFns__$3},__vue_inject_styles__$3,__vue_script__$3,__vue_scope_id__$3,__vue_is_functional_template__$3,__vue_module_identifier__$3,false,undefined,undefined,undefined);//
var script$4={components:{SourceNavItem:SourceNavItem},computed:_objectSpread({},index_esm.mapGetters(['isSidebarHidden','cropFiles','fromSources','mobileNavActive']),{sidebarClasses:function sidebarClasses(){return{'fsp-modal__sidebar--mobile':this.mobileNavActive,'fsp-modal__sidebar':true};}})};/* script */var __vue_script__$4=script$4;/* template */var __vue_render__$4=function __vue_render__$4(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return!_vm.cropFiles&&!_vm.isSidebarHidden?_c("div",{"class":_vm.sidebarClasses},[_c("div",{staticClass:"fsp-source-list"},_vm._l(_vm.fromSources,function(source){return _c("source-nav-item",{key:source.name,attrs:{"source-name":source.name,"source-label":source.label}});}),1)]):_vm._e();};var __vue_staticRenderFns__$4=[];__vue_render__$4._withStripped=true;/* style */var __vue_inject_styles__$4=undefined;/* scoped */var __vue_scope_id__$4=undefined;/* module identifier */var __vue_module_identifier__$4=undefined;/* functional template */var __vue_is_functional_template__$4=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Sidebar=normalizeComponent({render:__vue_render__$4,staticRenderFns:__vue_staticRenderFns__$4},__vue_inject_styles__$4,__vue_script__$4,__vue_scope_id__$4,__vue_is_functional_template__$4,__vue_module_identifier__$4,false,undefined,undefined,undefined);//
var script$5={computed:_objectSpread({},index_esm.mapGetters(['isInlineDisplay','whitelabel']))};/* script */var __vue_script__$5=script$5;/* template */var __vue_render__$5=function __vue_render__$5(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return!_vm.isInlineDisplay?_c("div",{staticClass:"fsp-picker__brand-container"},[_c("transition",{attrs:{name:"fsp-picker-fade"}},[_c("div",{directives:[{name:"show",rawName:"v-show",value:!_vm.isInlineDisplay&&!_vm.whitelabel,expression:"!isInlineDisplay && !whitelabel"}],staticClass:"fsp-picker__brand"},[_vm._v("\n      Powered by "),_c("span",{staticClass:"fsp-icon--filestack"}),_vm._v(" Filestack\n    ")])])],1):_vm._e();};var __vue_staticRenderFns__$5=[];__vue_render__$5._withStripped=true;/* style */var __vue_inject_styles__$5=undefined;/* scoped */var __vue_scope_id__$5=undefined;/* module identifier */var __vue_module_identifier__$5=undefined;/* functional template */var __vue_is_functional_template__$5=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Branding=normalizeComponent({render:__vue_render__$5,staticRenderFns:__vue_staticRenderFns__$5},__vue_inject_styles__$5,__vue_script__$5,__vue_scope_id__$5,__vue_is_functional_template__$5,__vue_module_identifier__$5,false,undefined,undefined,undefined);//
var script$6={components:{Branding:Branding,Sidebar:Sidebar},computed:_objectSpread({},index_esm.mapGetters(['cropFiles','currentCloud','filesWaiting','isInlineDisplay','isSidebarHidden','route','uploadStarted']),{isCloud:function isCloud(){return Object.keys(this.currentCloud).length>0;},isLocal:function isLocal(){return this.route[1]==='local_file_system';},isWebcam:function isWebcam(){return['webcam','audio','video'].indexOf(this.route[1]!==-1);},isTransformer:function isTransformer(){return this.route[0]==='transform';},getContentClasses:function getContentClasses(){return{'fsp-content--selected-items':this.isTransformer||!this.isLocal&&!this.isWebcam&&(this.filesWaiting.length>0||this.uploadStarted),'fsp-content--transformer':this.isTransformer};},getModalClasses:function getModalClasses(){return{'fsp-modal__body--full-width':this.isSidebarHidden&&!this.isTransformer,'fsp-modal__body--transformer':this.isTransformer,'fsp-modal__body--sidebar-disabled':!this.isTransformer&&this.cropFiles};}}),methods:{closePicker:function closePicker(){this.$store.dispatch('cancelPick');this.$root.$destroy();},handleScroll:function handleScroll(){var _this7=this;// TODO: Move this somewhere else. Perhaps an infinite scroll component.
var ct=this.$refs.content;// if route is not pointing to cloud source do not call throttle
if(ct&&this.isCloud){var cur=ct.scrollHeight-Math.round(ct.scrollTop,10);var zones=[ct.clientHeight,ct.clientHeight-1,ct.clientHeight+1];var isNearBottom=zones.indexOf(cur)!==-1;this.getNext=this.getNext||lodash_throttle(function(){// prevent callback to run without current cloud
if(!_this7.isCloud){return;}_this7.$store.dispatch('fetchCloudPath',{name:_this7.currentCloud.name,path:_this7.currentCloud.lastPath,next:_this7.currentCloud.next,load:false});},3000);if(isNearBottom&&this.currentCloud.next&&!this.currentCloud.isLoading){this.getNext();}}}}};/* script */var __vue_script__$6=script$6;/* template */var __vue_render__$6=function __vue_render__$6(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-picker"},[_c("div",{staticClass:"fsp-modal"},[_c("span",{staticClass:"fsp-picker__close-button fsp-icon--close-modal",attrs:{tabindex:"0",title:_vm.t("Click here or hit ESC to close picker")},on:{click:_vm.closePicker,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.closePicker($event);}}}),_vm._v(" "),_vm._t("sidebar"),_vm._v(" "),_c("div",{staticClass:"fsp-modal__body","class":_vm.getModalClasses},[_vm._t("header"),_vm._v(" "),_c("div",{ref:"content",staticClass:"fsp-content","class":_vm.getContentClasses,on:{scroll:_vm.handleScroll}},[_vm._t("body")],2),_vm._v(" "),_vm._t("footer")],2)],2),_vm._v(" "),_c("branding")],1);};var __vue_staticRenderFns__$6=[];__vue_render__$6._withStripped=true;/* style */var __vue_inject_styles__$6=undefined;/* scoped */var __vue_scope_id__$6=undefined;/* module identifier */var __vue_module_identifier__$6=undefined;/* functional template */var __vue_is_functional_template__$6=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Modal=normalizeComponent({render:__vue_render__$6,staticRenderFns:__vue_staticRenderFns__$6},__vue_inject_styles__$6,__vue_script__$6,__vue_scope_id__$6,__vue_is_functional_template__$6,__vue_module_identifier__$6,false,undefined,undefined,undefined);//
var script$7={components:{ContentHeader:ContentHeader,Modal:Modal},computed:_objectSpread({},index_esm.mapGetters(['isBlocked']),{headerText:function headerText(){return this.isBlocked?'Application Blocked':'Application Unavailable';},titleText:function titleText(){return this.isBlocked?'This application is blocked':'This application is unavailable';},subheaderText:function subheaderText(){return this.isBlocked?'For some reason this application is blocked.':'For some reason this application is unavailable.';}})};/* script */var __vue_script__$7=script$7;/* template */var __vue_render__$7=function __vue_render__$7(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("modal",[_c("content-header",{attrs:{slot:"header","hide-menu":true},slot:"header"},[_c("span",{staticClass:"fsp-header-text--visible"},[_vm._v(_vm._s(_vm.t(_vm.headerText)))])]),_vm._v(" "),_c("div",{staticClass:"fsp-blocked__container",attrs:{slot:"body"},slot:"body"},[_c("div",{staticClass:"fsp-blocked__icon"}),_vm._v(" "),_c("div",{staticClass:"fsp-blocked__title"},[_vm._v(_vm._s(_vm.t(_vm.titleText)))]),_vm._v(" "),_c("div",{staticClass:"fsp-text__subheader"},[_vm._v(_vm._s(_vm.t(_vm.subheaderText)))]),_vm._v(" "),_c("div",{staticClass:"fsp-text__subheader"},[_vm._v("\n      Please contact the owner of this page.\n    ")])])],1);};var __vue_staticRenderFns__$7=[];__vue_render__$7._withStripped=true;/* style */var __vue_inject_styles__$7=undefined;/* scoped */var __vue_scope_id__$7=undefined;/* module identifier */var __vue_module_identifier__$7=undefined;/* functional template */var __vue_is_functional_template__$7=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Blocked=normalizeComponent({render:__vue_render__$7,staticRenderFns:__vue_staticRenderFns__$7},__vue_inject_styles__$7,__vue_script__$7,__vue_scope_id__$7,__vue_is_functional_template__$7,__vue_module_identifier__$7,false,undefined,undefined,undefined);//
var script$8={computed:_objectSpread({},index_esm.mapGetters(['notifications']),{getClasses:function getClasses(){return{'fsp-notifications__success':!!this.mostRecentNotification.success};},mostRecentNotification:function mostRecentNotification(){return this.notifications[this.notifications.length-1];}}),methods:_objectSpread({},index_esm.mapActions(['removeAllNotifications']))};/* script */var __vue_script__$8=script$8;/* template */var __vue_render__$8=function __vue_render__$8(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _vm.notifications.length>0?_c("div",{staticClass:"fsp-notifications__container","class":_vm.getClasses},[_c("div",{staticClass:"fsp-notifications__message"},[_vm._v("\n     "+_vm._s(_vm.t(_vm.mostRecentNotification.message,_vm.mostRecentNotification.params))+"\n  ")]),_vm._v(" "),_c("span",{staticClass:"fsp-icon fsp-notifications__close-button",on:{click:_vm.removeAllNotifications}})]):_vm._e();};var __vue_staticRenderFns__$8=[];__vue_render__$8._withStripped=true;/* style */var __vue_inject_styles__$8=undefined;/* scoped */var __vue_scope_id__$8=undefined;/* module identifier */var __vue_module_identifier__$8=undefined;/* functional template */var __vue_is_functional_template__$8=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Notifications=normalizeComponent({render:__vue_render__$8,staticRenderFns:__vue_staticRenderFns__$8},__vue_inject_styles__$8,__vue_script__$8,__vue_scope_id__$8,__vue_is_functional_template__$8,__vue_module_identifier__$8,false,undefined,undefined,undefined);var sources=[{name:'local_file_system',label:'My Device',ui:'local'},{name:'webcam',label:'Take Photo',ui:'webcam'},{name:'video',label:'Record Video',ui:'opentok'},{name:'audio',label:'Record Audio',ui:'opentok'},{name:'customsource',label:'Custom Source',ui:'cloud',layout:'list'},{name:'dropbox',label:'Dropbox',ui:'cloud',layout:'list'},{name:'facebook',label:'Facebook',ui:'cloud',layout:'hybrid'},{name:'instagram',label:'Instagram',ui:'cloud',layout:'grid'},{name:'box',label:'Box',ui:'cloud',layout:'list'},{name:'googledrive',label:'Google Drive',ui:'cloud',layout:'list'},{name:'github',label:'Github',ui:'cloud',layout:'list'},{name:'gmail',label:'Gmail',ui:'cloud',layout:'list'},{name:'picasa',label:'Google Photos',ui:'cloud',layout:'hybrid'},{name:'onedrive',label:'OneDrive',ui:'cloud',layout:'list'},{name:'onedriveforbusiness',label:'OneDrive Business',ui:'cloud',layout:'list'},{name:'clouddrive',label:'Cloud Drive',ui:'cloud',layout:'list',deprecated:true},{name:'imagesearch',label:'Web Search',ui:'imagesearch'},{name:'url',label:'Link (URL)',ui:'url'},{name:'tint',label:'TINT',ui:'cloud'}];var getByName=function getByName(name){var definition;sources.forEach(function(sourceDefinition){if(sourceDefinition.name===name){definition=sourceDefinition;}});if(!definition){throw new Error("Unknown source \"".concat(name,"\""));}if(definition.deprecated){console.warn("Source ".concat(definition.name," is deprecated"));}return definition;};// API payload doesn't give parent folder as a separate field
// This function will construct it so we can match files by folder paths
// Should return a path like /Folder1 for a file /Folder1/File
var getFolderPath=function getFolderPath(file){if(file.folder){return file.path;}var arr=file.path.split('/').map(function(s){return s.toLowerCase();});arr.pop();return"".concat(arr.join('/'),"/");};var isFileInFolder=function isFileInFolder(file,folder){var folderPath=getFolderPath(file);var path=folder.path.split('/').map(function(s){return s.toLowerCase();}).join('/');var pathWithTrail="".concat(path,"/");return folderPath===path||folderPath===pathWithTrail;};var _isImage=function isImage(file){var type=file.mimetype||file.type;return type&&type.indexOf('image/')!==-1;};var isEditableImage=function isEditableImage(file){var ext=file.name&&file.name.split('.').pop().toLowerCase();var type=file.type||file.mimetype;var hasExt=['bmp','jpg','jpeg','png','gif','svg'].indexOf(ext)>=0;var hasMime=['image/jpeg','image/jpg','image/png','image/bmp','image/gif','image/svg','image/svg+xml'].indexOf(type)>=0;return hasExt||hasMime;};var isSVG$1=function isSVG$1(file){var ext=file.name&&file.name.split('.').pop().toLowerCase();var type=file.type||file.mimetype;var hasMime=['image/svg','image/svg+xml'].indexOf(type)>=0;return ext==='svg'||hasMime;};var _isAudio=function isAudio(file){var type=file.mimetype||file.type;return type&&type.indexOf('audio/')!==-1;};//
var script$9={props:['files'],data:function data(){return{lastClicked:null};},computed:_objectSpread({},index_esm.mapGetters(['cloudFolders','filesWaiting','viewType']),{onlyFolders:function onlyFolders(){return this.files.filter(function(f){return f.folder;});},onlyFiles:function onlyFiles(){return this.files.filter(function(f){return!f.folder;});}}),methods:_objectSpread({},index_esm.mapActions(['setViewType','addFile','deselectFolder','goToDirectory']),{handleClickFile:function handleClickFile(ev,file){if(!this.lastClicked){this.lastClicked=file;}if(ev.shiftKey){var start=this.files.indexOf(file);var end=this.files.indexOf(this.lastClicked);var fromEl=Math.min(start,end);var toEl=Math.max(start,end)+1;for(var i=fromEl;i<toEl;i+=1){if(!this.files[i]||this.files[i]===this.lastClicked||this.files[i].state===this.lastClicked.state){continue;}this.addFile(this.files[i]);}this.lastClicked=file;return;}this.lastClicked=file;this.addFile(file);},handleFolderClick:function handleFolderClick(ev,folder){if(ev.shiftKey){this.handleClickFile(ev,folder);return;}this.goToDirectory(folder);},getIconClass:function getIconClass(cls,file){var _ref;return _ref={},_defineProperty(_ref,cls,!this.isSelected(file)),_defineProperty(_ref,"".concat(cls,"--selected"),this.isSelected(file)),_ref;},isAudio:function isAudio(file){return _isAudio(file);},isImage:function isImage(file){return _isImage(file);},isLoading:function isLoading(file){if(file.folder){return this.cloudFolders[file.path]&&this.cloudFolders[file.path].loading;}return false;},isSelected:function isSelected(file){if(file.folder){return this.getFileCount(file)>0;}return file.state;},getFileCount:function getFileCount(folder){return this.filesWaiting.filter(function(f){return isFileInFolder(f,folder);}).length;}})};/* script */var __vue_script__$9=script$9;/* template */var __vue_render__$9=function __vue_render__$9(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _vm.files.length>0?_c("div",{staticClass:"fsp-grid","class":"fps-grid__type-"+_vm.viewType},[_vm._l(_vm.onlyFolders,function(folder){return _c("div",{key:folder.path,staticClass:"fsp-grid__cell","class":{"fsp-grid__cell--selected":_vm.isSelected(folder)},attrs:{title:folder.name,tabindex:"0"},on:{click:function click($event){return _vm.handleFolderClick($event,folder);},keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.handleFolderClick($event,folder);}}},[_vm.isSelected(folder)?_c("span",{staticClass:"fsp-badge fsp-badge--bright fsp-badge--file"},[_vm._v("\n      "+_vm._s(_vm.getFileCount(folder))+"\n    ")]):_vm._e(),_vm._v(" "),!_vm.isSelected(folder)||_vm.viewType==="grid"?_c("span",{staticClass:"fsp-grid__icon","class":_vm.getIconClass("fsp-grid__icon-folder",folder)}):_vm._e(),_vm._v(" "),_c("span",{staticClass:"fsp-grid__text","class":{"fsp-grid__text--selected":_vm.isSelected(folder)}},[_vm._v(_vm._s(folder.name))]),_vm._v(" "),_vm.isSelected(folder)?_c("span",{staticClass:"fsp-grid__icon--selected",attrs:{title:"Deselect folder"},on:{click:function click($event){$event.stopPropagation();return _vm.deselectFolder(folder);}}}):_vm._e(),_vm._v(" "),!_vm.isLoading(folder)&&!_vm.isSelected(folder)?_c("span",{staticClass:"fsp-grid__icon-folder-add",attrs:{title:"Add folder"},on:{click:function click($event){$event.stopPropagation();return _vm.addFile(folder);}}}):_vm._e(),_vm._v(" "),_vm.isLoading(folder)?_c("div",{staticClass:"fsp-loading--folder"}):_vm._e()]);}),_vm._v(" "),_vm._l(_vm.onlyFiles,function(file){return _c("div",{key:file.path,staticClass:"fsp-grid__cell","class":{"fsp-grid__cell--selected":_vm.isSelected(file),"fsp-grid__cell--thumbnail":_vm.isImage(file)},attrs:{tabindex:"0",title:file.name},on:{keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.handleClickFile($event,file);},click:function click($event){return _vm.handleClickFile($event,file);}}},[_vm.isAudio(file)?_c("span",{staticClass:"fsp-grid__icon","class":_vm.getIconClass("fsp-grid__icon-audio",file)}):_vm.isImage(file)?_c("img",{staticClass:"fsp-grid__icon fsp-grid__thumbnail",attrs:{src:file.thumbnail,alt:file.name}}):file.mimetype==="application/pdf"?_c("span",{staticClass:"fsp-grid__icon","class":_vm.getIconClass("fsp-grid__icon-pdf",file)}):file.mimetype==="application/zip"?_c("span",{staticClass:"fsp-grid__icon","class":_vm.getIconClass("fsp-grid__icon-zip",file)}):_c("span",{staticClass:"fsp-grid__icon","class":_vm.getIconClass("fsp-grid__icon-file",file)}),_vm._v(" "),_c("span",{staticClass:"fsp-grid__text","class":{"fsp-grid__text--selected":_vm.isSelected(file)}},[_vm._v(_vm._s(file.name))]),_vm._v(" "),_vm.isSelected(file)?_c("span",{staticClass:"fsp-grid__icon--selected"}):_vm._e(),_vm._v(" "),_c("div",{staticClass:"fsp-grid__cell--dark"})]);})],2):_vm._e();};var __vue_staticRenderFns__$9=[];__vue_render__$9._withStripped=true;/* style */var __vue_inject_styles__$9=undefined;/* scoped */var __vue_scope_id__$9=undefined;/* module identifier */var __vue_module_identifier__$9=undefined;/* functional template */var __vue_is_functional_template__$9=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var GridArray=normalizeComponent({render:__vue_render__$9,staticRenderFns:__vue_staticRenderFns__$9},__vue_inject_styles__$9,__vue_script__$9,__vue_scope_id__$9,__vue_is_functional_template__$9,__vue_module_identifier__$9,false,undefined,undefined,undefined);//
//
//
//
//
//
//
var script$a={methods:{goBack:function goBack(){this.$store.commit('GO_BACK_WITH_ROUTE');}}};/* script */var __vue_script__$a=script$a;/* template */var __vue_render__$a=function __vue_render__$a(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-empty"},[_c("div",{staticClass:"fsp-empty__message"},[_vm._v(_vm._s(_vm.t("This folder is empty.")))]),_vm._v(" "),_c("span",{staticClass:"fsp-empty__back-button",on:{click:_vm.goBack}},[_vm._v(_vm._s(_vm.t("Go back")))])]);};var __vue_staticRenderFns__$a=[];__vue_render__$a._withStripped=true;/* style */var __vue_inject_styles__$a=undefined;/* scoped */var __vue_scope_id__$a=undefined;/* module identifier */var __vue_module_identifier__$a=undefined;/* functional template */var __vue_is_functional_template__$a=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var EmptyFolder=normalizeComponent({render:__vue_render__$a,staticRenderFns:__vue_staticRenderFns__$a},__vue_inject_styles__$a,__vue_script__$a,__vue_scope_id__$a,__vue_is_functional_template__$a,__vue_module_identifier__$a,false,undefined,undefined,undefined);//
var script$b={props:['files'],components:{GridArray:GridArray,EmptyFolder:EmptyFolder},computed:{folderIsEmpty:function folderIsEmpty(){return!this.$store.getters.currentCloud.isLoading&&!this.$store.getters.currentCloud.isErrored&&!this.$store.getters.currentCloudFiles.length;}}};/* script */var __vue_script__$b=script$b;/* template */var __vue_render__$b=function __vue_render__$b(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-cloud__folder-view"},[_vm.folderIsEmpty?_c("empty-folder"):_c("grid-array",{attrs:{files:_vm.files}})],1);};var __vue_staticRenderFns__$b=[];__vue_render__$b._withStripped=true;/* style */var __vue_inject_styles__$b=undefined;/* scoped */var __vue_scope_id__$b=undefined;/* module identifier */var __vue_module_identifier__$b=undefined;/* functional template */var __vue_is_functional_template__$b=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var CloudGrid=normalizeComponent({render:__vue_render__$b,staticRenderFns:__vue_staticRenderFns__$b},__vue_inject_styles__$b,__vue_script__$b,__vue_scope_id__$b,__vue_is_functional_template__$b,__vue_module_identifier__$b,false,undefined,undefined,undefined);//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
var script$c={props:['crumbs','onClick'],methods:{truncateCrumbs:function truncateCrumbs(crumbs){var newCrumbs=[].concat(crumbs[0]);var lastTwoCrumbs=crumbs.filter(function(crumb,i){return i>=crumbs.length-2;});newCrumbs.push.apply(newCrumbs,[{path:'',label:'...'}].concat(_toConsumableArray(lastTwoCrumbs)));return newCrumbs;},handleClick:function handleClick(crumb){if(crumb.path&&crumb.label){this.onClick(crumb);}}}};/* script */var __vue_script__$c=script$c;/* template */var __vue_render__$c=function __vue_render__$c(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-breadcrumb__container"},[_vm.crumbs.length<=3?_c("span",{staticStyle:{display:"flex"}},_vm._l(_vm.crumbs,function(crumb){return _c("span",{key:crumb.path,staticClass:"fsp-breadcrumb__label",on:{click:function click($event){return _vm.handleClick(crumb);}}},[_vm._v("\n      "+_vm._s(crumb.label)+"\n    ")]);}),0):_c("span",{staticStyle:{display:"flex"}},_vm._l(_vm.truncateCrumbs(_vm.crumbs),function(crumb){return _c("span",{key:crumb.path,staticClass:"fsp-breadcrumb__label",on:{click:function click($event){return _vm.handleClick(crumb);}}},[_vm._v("\n      "+_vm._s(crumb.label)+"\n    ")]);}),0)]);};var __vue_staticRenderFns__$c=[];__vue_render__$c._withStripped=true;/* style */var __vue_inject_styles__$c=undefined;/* scoped */var __vue_scope_id__$c=undefined;/* module identifier */var __vue_module_identifier__$c=undefined;/* functional template */var __vue_is_functional_template__$c=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Breadcrumbs=normalizeComponent({render:__vue_render__$c,staticRenderFns:__vue_staticRenderFns__$c},__vue_inject_styles__$c,__vue_script__$c,__vue_scope_id__$c,__vue_is_functional_template__$c,__vue_module_identifier__$c,false,undefined,undefined,undefined);//
//
//
//
//
//
var script$d={};/* script */var __vue_script__$d=script$d;/* template */var __vue_render__$d=function __vue_render__$d(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("transition",{attrs:{name:"fsp-loading--fade"}},[_c("div",{staticClass:"fsp-loading"})]);};var __vue_staticRenderFns__$d=[];__vue_render__$d._withStripped=true;/* style */var __vue_inject_styles__$d=undefined;/* scoped */var __vue_scope_id__$d=undefined;/* module identifier */var __vue_module_identifier__$d=undefined;/* functional template */var __vue_is_functional_template__$d=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Loading=normalizeComponent({render:__vue_render__$d,staticRenderFns:__vue_staticRenderFns__$d},__vue_inject_styles__$d,__vue_script__$d,__vue_scope_id__$d,__vue_is_functional_template__$d,__vue_module_identifier__$d,false,undefined,undefined,undefined);//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
var script$e={props:{clickFn:Function}};/* script */var __vue_script__$e=script$e;/* template */var __vue_render__$e=function __vue_render__$e(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("button",{staticClass:"fsp-button--authgoogle",attrs:{type:"button",tabindex:"0"},on:{click:_vm.clickFn}},[_c("svg",{staticClass:"svg-icon native iconGoogle",attrs:{"aria-hidden":"true",width:"20",height:"20",viewBox:"0 0 18 18"}},[_c("path",{attrs:{d:"M16.51 8H8.98v3h4.3c-.18 1-.74 1.48-1.6 2.04v2.01h2.6a7.8 7.8 0 0 0 2.38-5.88c0-.57-.05-.66-.15-1.18z",fill:"#4285F4"}}),_vm._v(" "),_c("path",{attrs:{d:"M8.98 17c2.16 0 3.97-.72 5.3-1.94l-2.6-2a4.8 4.8 0 0 1-7.18-2.54H1.83v2.07A8 8 0 0 0 8.98 17z",fill:"#34A853"}}),_vm._v(" "),_c("path",{attrs:{d:"M4.5 10.52a4.8 4.8 0 0 1 0-3.04V5.41H1.83a8 8 0 0 0 0 7.18l2.67-2.07z",fill:"#FBBC05"}}),_vm._v(" "),_c("path",{attrs:{d:"M8.98 4.18c1.17 0 2.23.4 3.06 1.2l2.3-2.3A8 8 0 0 0 1.83 5.4L4.5 7.49a4.77 4.77 0 0 1 4.48-3.3z",fill:"#EA4335"}})]),_vm._v(" "),_c("span",[_vm._v("\n    "+_vm._s(_vm.t("Sign in with Google"))+"\n  ")])]);};var __vue_staticRenderFns__$e=[];__vue_render__$e._withStripped=true;/* style */var __vue_inject_styles__$e=undefined;/* scoped */var __vue_scope_id__$e=undefined;/* module identifier */var __vue_module_identifier__$e=undefined;/* functional template */var __vue_is_functional_template__$e=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var GoogleSignInButton=normalizeComponent({render:__vue_render__$e,staticRenderFns:__vue_staticRenderFns__$e},__vue_inject_styles__$e,__vue_script__$e,__vue_scope_id__$e,__vue_is_functional_template__$e,__vue_module_identifier__$e,false,undefined,undefined,undefined);//
var script$f={components:{CloudGrid:CloudGrid,Breadcrumbs:Breadcrumbs,Loading:Loading,GoogleSignInButton:GoogleSignInButton},data:function data(){return{googleSources:['googledrive','gmail','picasa']};},computed:_objectSpread({},index_esm.mapGetters(['apiClient','cloudFolders','currentCloud','currentCloudFiles','customSourceName','viewType']),{currentDisplay:function currentDisplay(){return getByName(this.currentCloud.name);},currentLabel:function currentLabel(){if(this.currentCloud.name==='customsource'){return this.customSourceName||'Custom Source';}return this.currentDisplay.label;},currentCrumbs:function currentCrumbs(){var _this8=this;return this.currentCloud.path.map(function(path){if(path==='/'){return{label:_this8.currentLabel,path:path};}return{label:_this8.cloudFolders[path].name,path:path};});},currentSource:function currentSource(){if(!this.$store.getters.currentCloud||!Object.keys(this.$store.getters.currentCloud).length){return null;}return getByName(this.$store.getters.currentCloud.name);},customAuthTextTop:function customAuthTextTop(){var currentCloudName=this.currentSource.name;var config=this.$store.getters.config.customAuthText;if(!config){return null;}if(config[currentCloudName]&&config[currentCloudName].top){return config[currentCloudName].top;}if(config["default"]&&config["default"].top){return config["default"].top;}return null;},customAuthTextBottom:function customAuthTextBottom(){var currentCloudName=this.currentSource.name;var config=this.$store.getters.config.customAuthText;if(!config){return null;}if(config[currentCloudName]&&config[currentCloudName].bottom){return config[currentCloudName].bottom;}if(config["default"]&&config["default"].bottom){return config["default"].bottom;}return null;}}),methods:_objectSpread({},index_esm.mapActions(['fetchCloudPath','setViewType']),{toogleViewType:function toogleViewType(){var view=this.viewType==='list'?'grid':'list';this.$session.set('cloud-grid-view',view);this.setViewType(view);},getViewTypeIconClass:function getViewTypeIconClass(){return"fsp-cloud_view-type-icon-".concat(this.viewType==='list'?'grid':'list');},authorize:function authorize(){var _this9=this;var url=this.currentCloud.redirect;var win=window.open(url,'_blank');var waitUntilWindowClosed=function waitUntilWindowClosed(){setTimeout(function(){if(win&&win.closed!==true){setTimeout(waitUntilWindowClosed,100);}else{setTimeout(function(){_this9.fetchCloudPath({name:_this9.currentCloud.name});},100);}},1000);};waitUntilWindowClosed();},tryAgain:function tryAgain(){return this.fetchCloudPath({name:this.currentCloud.name});},updatePath:function updatePath(crumb){var index=this.currentCloud.path.indexOf(crumb.path);// Don't add path to route if we're already on that path
if(index===this.currentCloud.path.length-1){return;}var newPath=this.currentCloud.path.filter(function(path,i){return i<=index;});var rootPath=['source',this.currentCloud.name];if(crumb.path==='/'){this.$store.commit('CHANGE_ROUTE',rootPath);}else{rootPath.push(newPath);this.$store.commit('CHANGE_ROUTE',rootPath);}},shouldUseGoogleSignIn:function shouldUseGoogleSignIn(){if(this.currentSource.name){return this.googleSources.indexOf(this.currentSource.name)>-1;}return false;}}),mounted:function mounted(){var viewStorage=this.$session.get('cloud-grid-view');if(viewStorage){this.setViewType(viewStorage);}}};/* script */var __vue_script__$f=script$f;/* template */var __vue_render__$f=function __vue_render__$f(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-cloud__container"},[_vm.currentCloud.isLoading?_c("loading"):_vm._e(),_vm._v(" "),_vm.currentCloud.isErrored?_c("div",{staticClass:"fsp-cloud-error"},[_c("div",{staticClass:"fsp-cloud-error__text"},[_vm._v("Something went wrong.")]),_vm._v(" "),_c("div",{key:"retryCall",staticClass:"fsp-button fsp-button--outline",attrs:{tabindex:"0"},on:{click:_vm.tryAgain,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.tryAgain($event);}}},[_vm._v("\n      Retry\n    ")])]):_vm.currentCloud.isUnauthorized?_c("div",{staticClass:"fsp-source-auth__wrapper"},[_c("span",{staticClass:"fsp-icon fsp-icon--auth fsp-source-auth__el","class":"fsp-icon--"+_vm.currentCloud.name}),_vm._v(" "),_c("div",{staticClass:"fsp-text__title fsp-source-auth__el"},[_vm._v("\n      "+_vm._s(_vm.t("Select Files from {providerName}").replace("{providerName}",_vm.currentDisplay.label))+"\n    ")]),_vm._v(" "),_c("div",{staticClass:"fsp-source-auth__el"},[_vm.customAuthTextTop?_vm._l(_vm.customAuthTextTop,function(textLine){return _c("div",{key:textLine,staticClass:"fsp-custom-auth-top__container"},[_c("div",{staticClass:"fsp-text__subheader"},[_vm._v("\n            "+_vm._s(textLine)+"\n          ")])]);}):[_c("div"),_vm._v(" "),_c("div",{staticClass:"fsp-text__subheader"},[_vm._v("\n          "+_vm._s(_vm.t("You need to authenticate with {providerName}.").replace("{providerName}",_vm.currentDisplay.label))+"\n        ")]),_vm._v(" "),_c("div",{staticClass:"fsp-text__subheader"},[_vm._v("\n          "+_vm._s(_vm.t("We only extract images and never modify or delete them."))+"\n        ")])]],2),_vm._v(" "),_vm.shouldUseGoogleSignIn()?_c("GoogleSignInButton",{attrs:{clickFn:_vm.authorize}}):_c("button",{staticClass:"fsp-button fsp-button--auth fsp-source-auth__el",attrs:{type:"button",tabindex:"0"},on:{click:_vm.authorize}},[_vm._v("\n      "+_vm._s(_vm.t("Connect {providerName}").replace("{providerName}",_vm.currentDisplay.label))+"\n    ")]),_vm._v(" "),_c("div",{staticClass:"fsp-source-auth__el"},[_vm.customAuthTextBottom?_vm._l(_vm.customAuthTextBottom,function(textLine){return _c("div",{key:textLine,staticClass:"fsp-custom-auth-bottom__container"},[_c("div",{staticClass:"fsp-text__subheader"},[_vm._v("\n            "+_vm._s(textLine)+"\n          ")])]);}):[_c("div",{staticClass:"fsp-text__subheader"},[_vm._v("\n          "+_vm._s(_vm.t("A new page will open to connect your account."))+"\n        ")]),_vm._v(" "),_c("div",{staticClass:"fsp-text__subheader"},[_vm._v("\n          "+_vm._s(_vm.t('To disconnect from {providerName} click "Sign out" button in the menu.').replace("{providerName}",_vm.currentDisplay.label))+"\n        ")])]],2)],1):!_vm.currentCloud.isLoading?_c("div",{staticClass:"fsp-cloud__files-container"},[_c("div",{staticClass:"fsp-cloud__breadcrumbs"},[_vm.currentDisplay.layout!=="grid"?_c("breadcrumbs",{attrs:{crumbs:_vm.currentCrumbs,"on-click":_vm.updatePath}}):_vm._e(),_vm._v(" "),_c("div",{staticClass:"fsp-cloud_view-type",on:{click:function click($event){$event.preventDefault();return _vm.toogleViewType();}}},[_c("span",{staticClass:"fsp-cloud_view-type-icon","class":this.getViewTypeIconClass()})])],1),_vm._v(" "),_c("cloud-grid",{attrs:{files:_vm.currentCloudFiles}})],1):_vm._e()],1);};var __vue_staticRenderFns__$f=[];__vue_render__$f._withStripped=true;/* style */var __vue_inject_styles__$f=undefined;/* scoped */var __vue_scope_id__$f=undefined;/* module identifier */var __vue_module_identifier__$f=undefined;/* functional template */var __vue_is_functional_template__$f=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Cloud=normalizeComponent({render:__vue_render__$f,staticRenderFns:__vue_staticRenderFns__$f},__vue_inject_styles__$f,__vue_script__$f,__vue_scope_id__$f,__vue_is_functional_template__$f,__vue_module_identifier__$f,false,undefined,undefined,undefined);//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
//
var script$g={props:['isVisible','fullWidth']};/* script */var __vue_script__$g=script$g;/* template */var __vue_render__$g=function __vue_render__$g(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-footer","class":{"fsp-footer--appeared":_vm.isVisible}},[_c("div",{staticClass:"fsp-footer__nav"},[_c("span",{staticClass:"fsp-footer__nav--left"},[_vm._t("nav-left")],2),_vm._v(" "),_c("span",{staticClass:"fsp-footer__nav--center",style:{width:_vm.fullWidth?"100%":null}},[_vm._t("nav-center")],2),_vm._v(" "),_c("span",{staticClass:"fsp-footer__nav--right"},[_vm._t("nav-right")],2)])]);};var __vue_staticRenderFns__$g=[];__vue_render__$g._withStripped=true;/* style */var __vue_inject_styles__$g=undefined;/* scoped */var __vue_scope_id__$g=undefined;/* module identifier */var __vue_module_identifier__$g=undefined;/* functional template */var __vue_is_functional_template__$g=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var FooterNav=normalizeComponent({render:__vue_render__$g,staticRenderFns:__vue_staticRenderFns__$g},__vue_inject_styles__$g,__vue_script__$g,__vue_scope_id__$g,__vue_is_functional_template__$g,__vue_module_identifier__$g,false,undefined,undefined,undefined);//
var script$h={components:{GridArray:GridArray,Loading:Loading},computed:_objectSpread({},index_esm.mapGetters(['isSearching','noResultsFound','resultsFound','imageSearchInput','imageSearchResults','filesWaiting','viewType']),{placeholderText:function placeholderText(){return"".concat(this.t('Search images'),"...");}}),mounted:function mounted(){this.oldViewType=this.viewType;this.setViewType('grid');},destroyed:function destroyed(){this.setViewType(this.oldViewType);},methods:_objectSpread({},index_esm.mapActions(['updateSearchInput','fetchImages','setViewType']),{fetch:function fetch(){this.fetchImages();this.$refs.searchInput.blur();},updateInput:function updateInput(ev){this.updateSearchInput(ev.target.value);},clearSearch:function clearSearch(){this.updateSearchInput('');}})};/* script */var __vue_script__$h=script$h;/* template */var __vue_render__$h=function __vue_render__$h(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-image-search"},[_vm.isSearching?_c("loading"):_vm._e(),_vm._v(" "),_c("div",{staticClass:"fsp-image-search__form-container","class":{"fsp-image-search__form-container--results":_vm.resultsFound}},[_c("form",{staticClass:"fsp-url-source__form",on:{submit:function submit($event){$event.preventDefault();return _vm.fetch($event);}}},[_c("input",{ref:"searchInput",staticClass:"fsp-url-source__input",attrs:{placeholder:_vm.placeholderText,disabled:_vm.isSearching,tabindex:"0"},domProps:{value:_vm.imageSearchInput},on:{input:_vm.updateInput}}),_vm._v(" "),_vm._m(0)])]),_vm._v(" "),_c("div",{staticClass:"fsp-image-search__results","class":{"fsp-content--selected-items":_vm.resultsFound&&_vm.filesWaiting.length}},[_vm.resultsFound?_c("grid-array",{staticStyle:{"padding-top":"0px"},attrs:{files:_vm.imageSearchResults}}):_vm._e()],1)],1);};var __vue_staticRenderFns__$h=[function(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("button",{staticClass:"fsp-button fsp-url-source__submit-button",attrs:{type:"submit",tabindex:"0"}},[_c("div",{staticClass:"fsp-icon fsp-image-search__submit-icon"})]);}];__vue_render__$h._withStripped=true;/* style */var __vue_inject_styles__$h=undefined;/* scoped */var __vue_scope_id__$h=undefined;/* module identifier */var __vue_module_identifier__$h=undefined;/* functional template */var __vue_is_functional_template__$h=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var ImageSearch=normalizeComponent({render:__vue_render__$h,staticRenderFns:__vue_staticRenderFns__$h},__vue_inject_styles__$h,__vue_script__$h,__vue_scope_id__$h,__vue_is_functional_template__$h,__vue_module_identifier__$h,false,undefined,undefined,undefined);//
var script$i={computed:_objectSpread({},index_esm.mapGetters(['selectLabelIsActive']))};/* script */var __vue_script__$i=script$i;/* template */var __vue_render__$i=function __vue_render__$i(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-select-labels","class":{"fsp-select-labels--active":_vm.selectLabelIsActive}},[_c("div",{staticClass:"fsp-drop-area__title fsp-text__title"},[_vm._v("\n      "+_vm._s(_vm.t("Select Files to Upload"))+"\n  ")]),_vm._v(" "),_c("div",{staticClass:"fsp-drop-area__subtitle fsp-text__subheader"},[_vm._v("\n      "+_vm._s(_vm.t("or Drag and Drop, Copy and Paste Files"))+"\n  ")])]);};var __vue_staticRenderFns__$i=[];__vue_render__$i._withStripped=true;/* style */var __vue_inject_styles__$i=undefined;/* scoped */var __vue_scope_id__$i=undefined;/* module identifier */var __vue_module_identifier__$i=undefined;/* functional template */var __vue_is_functional_template__$i=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var SelectFilesLabel=normalizeComponent({render:__vue_render__$i,staticRenderFns:__vue_staticRenderFns__$i},__vue_inject_styles__$i,__vue_script__$i,__vue_scope_id__$i,__vue_is_functional_template__$i,__vue_module_identifier__$i,false,undefined,undefined,undefined);//
var script$j={components:{SelectFilesLabel:SelectFilesLabel},computed:_objectSpread({},index_esm.mapGetters(['accept','canAddMoreFiles','maxFiles']),{acceptStr:function acceptStr(){if(this.accept){return this.accept.join(',');}return undefined;},multiple:function multiple(){return this.maxFiles>1;}}),methods:_objectSpread({},index_esm.mapActions(['addFile','updateSelectLabelActive']),{clearEvent:function clearEvent(event){event.target.value=null;},onMouseover:function onMouseover(){this.updateSelectLabelActive(true);},onMouseout:function onMouseout(){this.updateSelectLabelActive(false);},onFilesSelected:function onFilesSelected(event){try{var files=event.target.files;if(!files.length){return;}for(var i=0;i<files.length;i+=1){this.addFile(files[i]);}}catch(e){console.error(event.target.files);throw e;}},openSelectFile:function openSelectFile(){this.$refs.fileUploadInput.click();}}),mounted:function mounted(){var dropArea=this.$refs.dropArea;if(dropArea){dropArea.addEventListener('mouseover',this.onMouseover);dropArea.addEventListener('mouseout',this.onMouseout);}}};/* script */var __vue_script__$j=script$j;/* template */var __vue_render__$j=function __vue_render__$j(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-drop-area-container"},[_c("div",{ref:"dropArea",staticClass:"fsp-drop-area",attrs:{tabindex:"0"},on:{click:_vm.openSelectFile,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.openSelectFile($event);}}},[_c("select-files-label"),_vm._v(" "),_c("input",{ref:"fileUploadInput",staticClass:"fsp-local-source__fileinput",attrs:{type:"file",id:"fsp-fileUpload",accept:_vm.acceptStr,multiple:_vm.multiple,disabled:!_vm.canAddMoreFiles},on:{change:function change($event){return _vm.onFilesSelected($event);},click:function click($event){return _vm.clearEvent($event);}}})],1)]);};var __vue_staticRenderFns__$j=[];__vue_render__$j._withStripped=true;/* style */var __vue_inject_styles__$j=undefined;/* scoped */var __vue_scope_id__$j=undefined;/* module identifier */var __vue_module_identifier__$j=undefined;/* functional template */var __vue_is_functional_template__$j=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Local=normalizeComponent({render:__vue_render__$j,staticRenderFns:__vue_staticRenderFns__$j},__vue_inject_styles__$j,__vue_script__$j,__vue_scope_id__$j,__vue_is_functional_template__$j,__vue_module_identifier__$j,false,undefined,undefined,undefined);//
var script$k={components:{FooterNav:FooterNav},data:function data(){return{pictureTaken:false,webCamImageSrc:'',webCamError:'',hasUserMedia:false};},methods:_objectSpread({},index_esm.mapActions(['addFile']),{webCamReady:function webCamReady(){var _this10=this;if(!navigator||!navigator.mediaDevices){this.webCamError='disabled';return;}navigator.mediaDevices.getUserMedia({video:true,audio:false}).then(function(stream){_this10.$refs.video.srcObject=stream;_this10.$refs.video.play();_this10.hasUserMedia=true;})["catch"](function(){_this10.webCamError='disabled';});},turnWebCamOff:function turnWebCamOff(){if(this.$refs.video&&this.$refs.video.srcObject){this.$refs.video.srcObject.getTracks().forEach(function(track){track.stop();});}},clearPhoto:function clearPhoto(){var canvas=document.createElement('canvas');var context=canvas.getContext('2d');context.fillRect(0,0,canvas.width,canvas.height);this.webCamImageSrc='';this.$refs.photo.setAttribute('src',this.webCamImageSrc);this.pictureTaken=false;this.webCamReady();},getPhoto:function getPhoto(){if(!this.hasUserMedia)return null;var video=this.$refs.video;var canvas=document.createElement('canvas');var context=canvas.getContext('2d');canvas.height=video.clientHeight;canvas.width=video.clientWidth;context.drawImage(video,0,0);this.webCamImageSrc=canvas.toDataURL();this.$refs.photo.setAttribute('src',this.webCamImageSrc);this.canvas=canvas;this.pictureTaken=true;this.turnWebCamOff();return canvas;},addPhoto:function addPhoto(){var _this11=this;var lang=this.$store.getters.lang;this.canvas.toBlob(function(blob){blob.name="webcam-".concat(new Date().toLocaleString(lang),".png");_this11.addFile(blob);});}}),beforeMount:function beforeMount(){if(navigator.mediaDevices){this.webCamReady();}else{this.webCamError='browser';}this.pictureTaken=false;},beforeDestroy:function beforeDestroy(){this.turnWebCamOff();}};/* script */var __vue_script__$k=script$k;/* template */var __vue_render__$k=function __vue_render__$k(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-webcam__container"},[_vm.webCamError?_c("div",[_vm.webCamError==="disabled"?_c("div",{staticClass:"fsp-source-auth__wrapper"},[_c("span",{staticClass:"fsp-icon fsp-icon--auth fsp-source-auth__el fsp-icon--webcam-disabled"}),_vm._v(" "),_c("div",{staticClass:"fsp-text__title fsp-source-auth__el"},[_vm._v(_vm._s(_vm.t("Webcam Disabled")))]),_vm._v(" "),_c("div",{staticClass:"fsp-source-auth__el"},[_c("span",{staticClass:"fsp-text__subheader"},[_vm._v("\n          "+_vm._s(_vm.t("Please enable your webcam to take a photo."))+"\n        ")])])]):_vm._e(),_vm._v(" "),_vm.webCamError==="browser"?_c("div",{staticClass:"fsp-source-auth__wrapper"},[_c("span",{staticClass:"fsp-icon fsp-icon--auth fsp-source-auth__el fsp-icon--webcam-disabled"}),_vm._v(" "),_c("div",{staticClass:"fsp-text__title fsp-source-auth__el"},[_vm._v(_vm._s(_vm.t("Webcam Not Supported")))]),_vm._v(" "),_c("div",{staticClass:"fsp-source-auth__el"},[_c("span",{staticClass:"fsp-text__subheader"},[_vm._v("\n          "+_vm._s(_vm.t("Your current browser does not support webcam functionality."))+"\n        ")]),_vm._v(" "),_c("br"),_vm._v(" "),_c("span",{staticClass:"fsp-text__subheader"},[_vm._v("\n          "+_vm._s(_vm.t("We suggest using Chrome or Firefox."))+"\n        ")])])]):_vm._e()]):_vm._e(),_vm._v(" "),!_vm.webCamError?_c("div",{staticClass:"fsp-webcam"},[_c("img",{directives:[{name:"show",rawName:"v-show",value:_vm.pictureTaken,expression:"pictureTaken"}],ref:"photo",staticClass:"fsp-webcam__image"}),_vm._v(" "),_c("video",{directives:[{name:"show",rawName:"v-show",value:!_vm.pictureTaken,expression:"!pictureTaken"}],ref:"video",staticClass:"fsp-webcam__video",attrs:{src:_vm.src}})]):_vm._e(),_vm._v(" "),!_vm.webCamError?_c("footer-nav",{attrs:{slot:"footer","is-visible":true},slot:"footer"},[_c("span",{attrs:{slot:"nav-center"},slot:"nav-center"},[!_vm.pictureTaken?_c("span",{staticClass:"fsp-button fsp-button--primary fsp-button--webcam",on:{click:_vm.getPhoto}},[_c("span",{staticClass:"fsp-icon--webcam-start"})]):_vm._e(),_vm._v(" "),_vm.pictureTaken?_c("span",{staticClass:"fsp-button fsp-button--cancel fsp-button--webcam",on:{click:_vm.clearPhoto}},[_c("span",{staticClass:"fsp-icon--webcam-stop"})]):_vm._e()]),_vm._v(" "),_vm.pictureTaken?_c("span",{staticClass:"fsp-button fsp-button--primary",attrs:{slot:"nav-right",title:"Next"},on:{click:_vm.addPhoto},slot:"nav-right"},[_vm._v("\n          "+_vm._s(_vm.t("Next"))+"\n    ")]):_vm._e()]):_vm._e()],1);};var __vue_staticRenderFns__$k=[];__vue_render__$k._withStripped=true;/* style */var __vue_inject_styles__$k=undefined;/* scoped */var __vue_scope_id__$k=undefined;/* module identifier */var __vue_module_identifier__$k=undefined;/* functional template */var __vue_is_functional_template__$k=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Webcam=normalizeComponent({render:__vue_render__$k,staticRenderFns:__vue_staticRenderFns__$k},__vue_inject_styles__$k,__vue_script__$k,__vue_scope_id__$k,__vue_is_functional_template__$k,__vue_module_identifier__$k,false,undefined,undefined,undefined);//
var script$l={components:{Loading:Loading},data:function data(){return{url:''};},computed:{isUrlFetching:function isUrlFetching(){return this.$store.getters.isUrlFetching;}},methods:{fetchUrl:function fetchUrl(){var _this12=this;if(this.url){this.$store.dispatch('fetchUrl',this.url).then(function(res){if(res&&_this12.$store.getters.maxFiles>1){_this12.$store.commit('CHANGE_ROUTE',['summary']);}});}}}};/* script */var __vue_script__$l=script$l;/* template */var __vue_render__$l=function __vue_render__$l(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-url-source"},[_vm.isUrlFetching?_c("loading"):_vm._e(),_vm._v(" "),_c("div",{staticClass:"fsp-url-source__container"},[_c("form",{staticClass:"fsp-url-source__form",on:{submit:function submit($event){$event.preventDefault();return _vm.fetchUrl($event);}}},[_c("input",{directives:[{name:"model",rawName:"v-model",value:_vm.url,expression:"url"}],staticClass:"fsp-url-source__input",attrs:{type:"url",placeholder:_vm.t("Enter a URL"),tabindex:"0"},domProps:{value:_vm.url},on:{input:function input($event){if($event.target.composing){return;}_vm.url=$event.target.value;}}}),_vm._v(" "),_vm._m(0)])])],1);};var __vue_staticRenderFns__$l=[function(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("button",{staticClass:"fsp-button fsp-url-source__submit-button",attrs:{type:"submit",tabindex:"0"}},[_c("div",{staticClass:"fsp-icon fsp-url-source__submit-icon"})]);}];__vue_render__$l._withStripped=true;/* style */var __vue_inject_styles__$l=undefined;/* scoped */var __vue_scope_id__$l=undefined;/* module identifier */var __vue_module_identifier__$l=undefined;/* functional template */var __vue_is_functional_template__$l=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Url=normalizeComponent({render:__vue_render__$l,staticRenderFns:__vue_staticRenderFns__$l},__vue_inject_styles__$l,__vue_script__$l,__vue_scope_id__$l,__vue_is_functional_template__$l,__vue_module_identifier__$l,false,undefined,undefined,undefined);//
var script$m={props:{type:String},components:{FooterNav:FooterNav,Loading:Loading},computed:_objectSpread({},index_esm.mapGetters(['getModuleUrl']),{client:function client(){return this.$store.getters.cloudClient;},filesList:function filesList(){return this.$store.getters.filesList;},maxFiles:function maxFiles(){return this.$store.getters.maxFiles;},routesHistory:function routesHistory(){return this.$store.getters.routesHistory;},isErrored:function isErrored(){return['browsererror','scripterror','accessDenied'].indexOf(this.state)!==-1;}}),data:function data(){return{archiveId:null,attempt:0,state:'connecting',startButtonInit:false,publisher:null,session:null,otSession:{key:null,ot_session_id:null,ot_token:null},publisherOptions:{insertMode:'append',width:'100%',height:'100%'}};},methods:_objectSpread({},index_esm.mapActions(['addFile']),{goToSummary:function goToSummary(){this.$store.commit('CHANGE_ROUTE',['summary']);this.$store.dispatch('updateMobileNavActive',false);},loadOpenTok:function loadOpenTok(){var _this13=this;this.state='connecting';return loadModule("".concat(this.getModuleUrl('fs-opentok'),"?").concat(this.attempt),'fs-opentok')["catch"](function(){_this13.state='scripterror';});},reset:function reset(){var _this14=this;if(this.session){if(this.publisher)this.session.unpublish(this.publisher);this.session.disconnect();}this.attempt+=1;this.loadOpenTok().then(function(tok){_this14.initialize(tok);});},initialize:function initialize(tok){var _this15=this;if(!tok){console.error('Cannot initialize opentok');return;}this.client.tokInit(this.$props.type).then(function(res){_this15.otSession=res;var pubOptions=_objectSpread({},_this15.publisherOptions,{mirror:false,resolution:_this15.$store.getters.videoResolution,publishVideo:_this15.$props.type==='video'});_this15.session=tok.initSession(_this15.otSession.key,_this15.otSession.ot_session_id);_this15.session.connect(_this15.otSession.ot_token,function(err){if(!err&&_this15.$refs&&_this15.$refs.video){_this15.publisher=tok.initPublisher(_this15.$refs.video,pubOptions);_this15.session.publish(_this15.publisher);_this15.publisher.on('accessAllowed',function(){_this15.state='ready';navigator.mediaDevices.getUserMedia({video:true}).then(function(){_this15.startButtonInit=true;});});_this15.publisher.on('accessDenied',function(){_this15.state='accessDenied';});}else{_this15.state='errored';console.warn('OT_ERROR',err);}});_this15.session.on('archiveStarted',function(evt){_this15.archiveId=evt.id;_this15.state='recording';});_this15.session.on('archiveStopped',function(evt){_this15.archiveId=evt.id;});})["catch"](function(){_this15.state='errored';});},start:function start(){var _this16=this;if(!this.startButtonInit){return;}this.state='recording';this.client.tokStart(this.$props.type,this.otSession.key,this.otSession.ot_session_id)["catch"](function(err){_this16.state='errored';console.error(err);});},stop:function stop(){var _this17=this;if(!this.archiveId){console.error('No archive ID');return;}this.state='ready';this.client.tokStop(this.$props.type,this.otSession.key,this.otSession.ot_session_id,this.archiveId).then(function(){var lang=_this17.$store.getters.lang;var date=new Date().toLocaleString(lang,{month:'short',day:'numeric',hour:'numeric',minute:'numeric',year:'numeric'});var ext=_this17.$props.type==='audio'?'mp3':'mp4';var mimetype=_this17.$props.type==='audio'?'audio/mp3':'video/mp4';_this17.addFile({source:_this17.$props.type,sourceKind:'cloud',path:"/".concat(_this17.archiveId,"/recording-").concat(date,".").concat(ext),name:"recording-".concat(date,".").concat(ext),mimetype:mimetype});if(_this17.maxFiles>1){_this17.goToSummary();}})["catch"](function(err){_this17.state='errored';console.error(err);});}}),beforeMount:function beforeMount(){if(!navigator.mediaDevices){this.state='browsererror';}},mounted:function mounted(){var _this18=this;if(this.state!=='browsererror'){this.loadOpenTok().then(function(tok){_this18.initialize(tok);});}},destroyed:function destroyed(){if(this.session){if(this.publisher)this.session.unpublish(this.publisher);this.session.disconnect();}},watch:{type:function type(){var _this19=this;if(this.session){if(this.publisher)this.session.unpublish(this.publisher);this.session.disconnect();this.loadOpenTok().then(function(tok){_this19.initialize(tok);});}}}};/* script */var __vue_script__$m=script$m;/* template */var __vue_render__$m=function __vue_render__$m(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-video"},[_vm.filesList.length>0?_c("div",{staticClass:"fsp-summary__go-back",attrs:{title:"Go to summary"},on:{click:_vm.goToSummary}}):_vm._e(),_vm._v(" "),_vm.state==="connecting"?_c("loading"):_vm.state==="accessDenied"?_c("div",{staticClass:"fsp-source-auth__wrapper"},[_c("span",{staticClass:"fsp-icon fsp-icon--auth fsp-source-auth__el fsp-icon--video-disabled"}),_vm._v(" "),_c("div",{staticClass:"fsp-text__title fsp-source-auth__el"},[_vm._v("\n      "+_vm._s(_vm.t("Webcam Disabled"))+"\n    ")]),_vm._v(" "),_c("div",{staticClass:"fsp-source-auth__el"},[_c("span",{staticClass:"fsp-text__subheader"},[_vm._v("\n        "+_vm._s(_vm.t("Please enable your webcam to record video or audio."))+"\n      ")])]),_vm._v(" "),_c("div",{key:"retryCam",staticClass:"fsp-button fsp-button--outline",on:{click:_vm.reset}},[_vm._v("\n      Retry\n    ")])]):_vm.state==="scripterror"?_c("div",{staticClass:"fsp-source-auth__wrapper"},[_c("span",{staticClass:"fsp-icon fsp-icon--auth fsp-source-auth__el fsp-icon--video-disabled"}),_vm._v(" "),_c("div",{staticClass:"fsp-text__title fsp-source-auth__el"},[_vm._v("\n      "+_vm._s("Failed to load "+_vm.type)+"\n    ")]),_vm._v(" "),_c("div",{staticClass:"fsp-source-auth__el"},[_c("div",{key:"retryCall",staticClass:"fsp-button fsp-button--outline",on:{click:_vm.reset}},[_vm._v("\n        Retry\n      ")])])]):_vm.state==="browsererror"?_c("div",{staticClass:"fsp-source-auth__wrapper"},[_c("span",{staticClass:"fsp-icon fsp-icon--auth fsp-source-auth__el fsp-icon--webcam-disabled"}),_vm._v(" "),_c("div",{staticClass:"fsp-text__title fsp-source-auth__el"},[_vm._v("\n      "+_vm._s(_vm.t("Webcam Not Supported"))+"\n    ")]),_vm._v(" "),_c("div",{staticClass:"fsp-source-auth__el"},[_c("span",{staticClass:"fsp-text__subheader"},[_vm._v("\n        "+_vm._s(_vm.t("Your current browser does not support webcam functionality."))+"\n      ")]),_vm._v(" "),_c("br"),_vm._v(" "),_c("span",{staticClass:"fsp-text__subheader"},[_vm._v("\n        "+_vm._s(_vm.t("We suggest using Chrome or Firefox."))+"\n      ")])])]):_vm._e(),_vm._v(" "),_c("div",{ref:"video",staticClass:"fsp-video__container"}),_vm._v(" "),_c("footer-nav",{attrs:{slot:"footer","is-visible":_vm.state!=="connecting"&&!_vm.isErrored},slot:"footer"},[_c("span",{attrs:{slot:"nav-left"},slot:"nav-left"}),_vm._v(" "),_c("span",{attrs:{slot:"nav-center"},slot:"nav-center"},[_vm.state==="ready"?_c("span",{staticClass:"fsp-button fsp-button--primary fsp-button--video","class":{"fsp-button--disabled":!_vm.startButtonInit},attrs:{title:"Start"},on:{click:_vm.start}},[_c("span",{staticClass:"fsp-icon--video-start"})]):_vm._e(),_vm._v(" "),_vm.state==="recording"?_c("span",{staticClass:"fsp-button fsp-button--cancel fsp-button--video",attrs:{title:"Stop"},on:{click:_vm.stop}},[_c("span",{staticClass:"fsp-icon--video-stop"})]):_vm._e()])])],1);};var __vue_staticRenderFns__$m=[];__vue_render__$m._withStripped=true;/* style */var __vue_inject_styles__$m=undefined;/* scoped */var __vue_scope_id__$m=undefined;/* module identifier */var __vue_module_identifier__$m=undefined;/* functional template */var __vue_is_functional_template__$m=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var OpenTok=normalizeComponent({render:__vue_render__$m,staticRenderFns:__vue_staticRenderFns__$m},__vue_inject_styles__$m,__vue_script__$m,__vue_scope_id__$m,__vue_is_functional_template__$m,__vue_module_identifier__$m,false,undefined,undefined,undefined);//
var script$n={components:{Cloud:Cloud,ContentHeader:ContentHeader,FooterNav:FooterNav,ImageSearch:ImageSearch,Local:Local,Modal:Modal,OpenTok:OpenTok,Sidebar:Sidebar,Url:Url,Webcam:Webcam},computed:_objectSpread({},index_esm.mapGetters(['canStartUpload','filesNeededCount','filesWaiting','mobileNavActive','minFiles','route']),{currentSource:function currentSource(){var sourceName=this.route[1];return getByName(sourceName);},minFilesMessage:function minFilesMessage(){if(this.filesNeededCount===1){return"".concat(this.t('Add')," 1 ").concat(this.t('more file'));}if(this.filesNeededCount>1){return"".concat(this.t('Add')," ").concat(this.filesNeededCount," ").concat(this.t('more files'));}return null;},isInsideCloudFolder:function isInsideCloudFolder(){return this.currentSource.ui==='cloud'&&!this.mobileNavActive&&this.route[2]&&this.route[2].length;}}),methods:_objectSpread({},index_esm.mapActions(['deselectAllFiles','updateMobileNavActive']),{goBack:function goBack(){this.$store.commit('GO_BACK_WITH_ROUTE_CURRENT_TYPE');},goToSummary:function goToSummary(){if(this.canStartUpload){this.$store.commit('CHANGE_ROUTE',['summary']);this.updateMobileNavActive(false);}}})};/* script */var __vue_script__$n=script$n;/* template */var __vue_render__$n=function __vue_render__$n(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("modal",[_c("div",{attrs:{slot:"header"},slot:"header"},[_vm.isInsideCloudFolder?_c("div",{staticClass:"fsp-summary__go-back",attrs:{title:_vm.t("Go back")},on:{click:_vm.goBack}}):_vm._e(),_vm._v(" "),_c("content-header",{attrs:{source:_vm.currentSource}})],1),_vm._v(" "),_c("sidebar",{attrs:{slot:"sidebar"},slot:"sidebar"}),_vm._v(" "),_vm.currentSource.ui==="local"?_c("local",{attrs:{slot:"body"},slot:"body"}):_vm._e(),_vm._v(" "),_vm.currentSource.ui==="cloud"?_c("cloud",{attrs:{slot:"body"},slot:"body"}):_vm._e(),_vm._v(" "),_vm.currentSource.ui==="webcam"?_c("webcam",{attrs:{slot:"body"},slot:"body"}):_vm._e(),_vm._v(" "),_vm.currentSource.ui==="opentok"?_c("open-tok",{attrs:{slot:"body",type:_vm.currentSource.name},slot:"body"}):_vm._e(),_vm._v(" "),_vm.currentSource.ui==="imagesearch"?_c("image-search",{attrs:{slot:"body"},slot:"body"}):_vm._e(),_vm._v(" "),_vm.currentSource.ui==="url"?_c("url",{attrs:{slot:"body"},slot:"body"}):_vm._e(),_vm._v(" "),_c("footer-nav",{attrs:{slot:"footer","is-visible":_vm.filesWaiting.length>0&&_vm.currentSource.ui!=="webcam"&&_vm.currentSource.ui!=="opentok"},slot:"footer"},[_c("span",{staticClass:"fsp-footer-text",attrs:{slot:"nav-left"},slot:"nav-left"},[_c("span",[_vm._v(_vm._s(_vm.t("Selected Files"))+": "+_vm._s(_vm.filesWaiting.length))])]),_vm._v(" "),_c("span",{staticClass:"fsp-button fsp-button--primary","class":{"fsp-button--disabled":!_vm.canStartUpload},attrs:{slot:"nav-right",title:"Next",tabindex:"0"},on:{click:_vm.goToSummary,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.goToSummary($event);}},slot:"nav-right"},[!_vm.canStartUpload&&_vm.filesWaiting.length!==0?_c("span",[_vm._v("\n        "+_vm._s(_vm.minFilesMessage)+"\n      ")]):_c("span",[_vm._v(_vm._s(_vm.t("View/Edit Selected")))])])])],1);};var __vue_staticRenderFns__$n=[];__vue_render__$n._withStripped=true;/* style */var __vue_inject_styles__$n=undefined;/* scoped */var __vue_scope_id__$n=undefined;/* module identifier */var __vue_module_identifier__$n=undefined;/* functional template */var __vue_is_functional_template__$n=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var PickFromSource=normalizeComponent({render:__vue_render__$n,staticRenderFns:__vue_staticRenderFns__$n},__vue_inject_styles__$n,__vue_script__$n,__vue_scope_id__$n,__vue_is_functional_template__$n,__vue_module_identifier__$n,false,undefined,undefined,undefined);var canvasToBlob=createCommonjsModule(function(module){(function(window){var CanvasPrototype=window.HTMLCanvasElement&&window.HTMLCanvasElement.prototype;var hasBlobConstructor=window.Blob&&function(){try{return Boolean(new Blob());}catch(e){return false;}}();var hasArrayBufferViewSupport=hasBlobConstructor&&window.Uint8Array&&function(){try{return new Blob([new Uint8Array(100)]).size===100;}catch(e){return false;}}();var BlobBuilder=window.BlobBuilder||window.WebKitBlobBuilder||window.MozBlobBuilder||window.MSBlobBuilder;var dataURIPattern=/^data:((.*?)(;charset=.*?)?)(;base64)?,/;var dataURLtoBlob=(hasBlobConstructor||BlobBuilder)&&window.atob&&window.ArrayBuffer&&window.Uint8Array&&function(dataURI){var matches,mediaType,isBase64,dataString,byteString,arrayBuffer,intArray,i,bb;// Parse the dataURI components as per RFC 2397
matches=dataURI.match(dataURIPattern);if(!matches){throw new Error('invalid data URI');}// Default to text/plain;charset=US-ASCII
mediaType=matches[2]?matches[1]:'text/plain'+(matches[3]||';charset=US-ASCII');isBase64=!!matches[4];dataString=dataURI.slice(matches[0].length);if(isBase64){// Convert base64 to raw binary data held in a string:
byteString=atob(dataString);}else{// Convert base64/URLEncoded data component to raw binary:
byteString=decodeURIComponent(dataString);}// Write the bytes of the string to an ArrayBuffer:
arrayBuffer=new ArrayBuffer(byteString.length);intArray=new Uint8Array(arrayBuffer);for(i=0;i<byteString.length;i+=1){intArray[i]=byteString.charCodeAt(i);}// Write the ArrayBuffer (or ArrayBufferView) to a blob:
if(hasBlobConstructor){return new Blob([hasArrayBufferViewSupport?intArray:arrayBuffer],{type:mediaType});}bb=new BlobBuilder();bb.append(arrayBuffer);return bb.getBlob(mediaType);};if(window.HTMLCanvasElement&&!CanvasPrototype.toBlob){if(CanvasPrototype.mozGetAsFile){CanvasPrototype.toBlob=function(callback,type,quality){var self=this;setTimeout(function(){if(quality&&CanvasPrototype.toDataURL&&dataURLtoBlob){callback(dataURLtoBlob(self.toDataURL(type,quality)));}else{callback(self.mozGetAsFile('blob',type));}});};}else if(CanvasPrototype.toDataURL&&dataURLtoBlob){CanvasPrototype.toBlob=function(callback,type,quality){var self=this;setTimeout(function(){callback(dataURLtoBlob(self.toDataURL(type,quality)));});};}}if(module.exports){module.exports=dataURLtoBlob;}else{window.dataURLtoBlob=dataURLtoBlob;}})(window);});//
//
//
//
//
//
//
//
var script$o={watch:{progress:function progress(num){this.$refs.bar.style.width="".concat(num,"%");}},props:['progress']};/* script */var __vue_script__$o=script$o;/* template */var __vue_render__$o=function __vue_render__$o(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-progress-bar"},[_c("div",{staticClass:"fsp-progress-bar__container"},[_c("div",{ref:"bar",staticClass:"fsp-progress-bar__bar",staticStyle:{width:"0"}})])]);};var __vue_staticRenderFns__$o=[];__vue_render__$o._withStripped=true;/* style */var __vue_inject_styles__$o=undefined;/* scoped */var __vue_scope_id__$o=undefined;/* module identifier */var __vue_module_identifier__$o=undefined;/* functional template */var __vue_is_functional_template__$o=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var ProgressBar=normalizeComponent({render:__vue_render__$o,staticRenderFns:__vue_staticRenderFns__$o},__vue_inject_styles__$o,__vue_script__$o,__vue_scope_id__$o,__vue_is_functional_template__$o,__vue_module_identifier__$o,false,undefined,undefined,undefined);/* eslint no-bitwise: ["error", { "allow": ["&"] }] */var toHexString=function toHexString(byteArray){var s='0x';byteArray.forEach(function(_byte){s+="0".concat((_byte&0xFF).toString(16)).slice(-2);});return s;};var findExifPosition=function findExifPosition(fileBuffer){var dataView=new DataView(fileBuffer);var length=fileBuffer.byteLength;var position={};var marker;var offset=2;var start;var end;if(dataView.getUint8(0)!==0xFF||dataView.getUint8(1)!==0xD8){// Not a valid jpeg
return;}while(offset<length){if(dataView.getUint8(offset)!==0xFF){// console.log("Not a valid marker at offset " + offset + ", found: " + dataView.getUint8(offset));
// Not a valid marker, something is wrong in the image structure. Better to terminate.
return;}marker=dataView.getUint8(offset+1);start=offset;end=offset+2+dataView.getUint16(offset+2);if(marker>=0xE1&&marker<=0xEF){// APPn marker found!
if(position.startOffset===undefined){position.startOffset=start;}position.endOffset=end;}else if(position.startOffset!==undefined){// We already collected some data, and now stumbled upon non-exif marker,
// what means we have everything what we wanted.
return position;// eslint-disable-line consistent-return
}else if(marker===0xDA){// We didn't find any data and after this marker all metadata has been read.
// No point in searching further.
return;}offset=end;}};var findWhereExifCanBePut=function findWhereExifCanBePut(fileBuffer){var dataView=new DataView(fileBuffer);var sof0Marker=0xC0;var sof2Marker=0xC2;var app0Marker=0xE0;var length=fileBuffer.byteLength;var offset=2;var marker;var end;var position;while(offset<length){marker=dataView.getUint8(offset+1);end=offset+2+dataView.getUint16(offset+2);if(marker===sof0Marker||marker===sof2Marker||marker===app0Marker){position={startOffset:end,endOffset:end};break;}offset=end;}return position;};var extractFrom=function extractFrom(fileBuffer){var position=findExifPosition(fileBuffer);if(!position){// This image has no exif data
return new ArrayBuffer(0);}return fileBuffer.slice(position.startOffset,position.endOffset);};var overwriteInFile=function overwriteInFile(targetFile,exifChunk){var targetExifPosition=findExifPosition(targetFile);if(!targetExifPosition){targetExifPosition=findWhereExifCanBePut(targetFile);}if(!targetExifPosition){// Couldn't find position in file where the APP data safely can be put.
// Aborting without introducing any changes to file.
return targetFile;}var header=targetFile.slice(0,targetExifPosition.startOffset);var rest=targetFile.slice(targetExifPosition.endOffset);var resultFile=new Uint8Array(header.byteLength+exifChunk.byteLength+rest.byteLength);resultFile.set(new Uint8Array(header),0);resultFile.set(new Uint8Array(exifChunk),header.byteLength);resultFile.set(new Uint8Array(rest),header.byteLength+exifChunk.byteLength);return resultFile.buffer;};// add orientation to file exif data
var generateExifOrientation=function generateExifOrientation(){var orientation=arguments.length>0&&arguments[0]!==undefined?arguments[0]:1;var standartExifString='ffe100004578696600004d4d002a0000000800010112000300000001000000000000';var exifBuffer=new Uint8Array(standartExifString.length/2);for(var i=0;i<standartExifString.length;i+=2){exifBuffer[i/2]=parseInt(standartExifString.substring(i,i+2),16);}var dw=new DataView(exifBuffer.buffer);dw.setUint16(dw.byteLength-6,orientation);dw.setUint16(2,dw.byteLength-2);// -2 exif preambule bytes
return dw.buffer;};var findExifStartPosition=function findExifStartPosition(file){var view=new DataView(file);var length=view.byteLength;var offset=2;while(offset<length){if(view.getUint16(offset+2,false)<=8){return false;}var marker=view.getUint16(offset,false);offset+=2;if(marker===0xffe1){offset+=2;if(view.getUint32(offset,false)!==0x45786966){return false;}var little=view.getUint16(offset+=6,false)===0x4949;offset+=view.getUint32(offset+4,little);var tags=view.getUint16(offset,little);offset+=2;// eslint-disable-next-line
for(var i=0;i<tags;i++){if(view.getUint16(offset+i*12,little)===0x0112){return{offset:offset+i*12+8,endian:little};}}// tslint:disable-next-line:no-bitwise
}else if((marker&0xff00)!==0xff00){break;}else{offset+=view.getUint16(offset,false);}}return false;};var getOrientation=function getOrientation(file){var view=new DataView(file);var exifPosition=findExifStartPosition(file);if(!exifPosition){return false;}return view.getUint16(exifPosition.offset,exifPosition.endian);};// method replace exif orientation with current one
var setOrientation=function setOrientation(file,orientation){var exifPosition=findExifStartPosition(file);if(!exifPosition){return file;}var view=new DataView(file);view.setUint16(exifPosition.offset,orientation,exifPosition.endian);return view.buffer;};var exif={toHexString:toHexString,extractFrom:extractFrom,overwriteInFile:overwriteInFile,setOrientation:setOrientation,getOrientation:getOrientation,generateExifOrientation:generateExifOrientation};var blobToArrayBuffer=function blobToArrayBuffer(blobFile){return new Promise(function(resolve,reject){if(!blobFile){return reject();}var reader=new FileReader();reader.onloadend=function(){resolve(reader.result);};reader.onerror=function(err){reject(err);};return reader.readAsArrayBuffer(blobFile);});};var FileUtils={blobToArrayBuffer:blobToArrayBuffer};/**
   * lodash (Custom Build) <https://lodash.com/>
   * Build: `lodash modularize exports="npm" -o ./`
   * Copyright jQuery Foundation and other contributors <https://jquery.org/>
   * Released under MIT license <https://lodash.com/license>
   * Based on Underscore.js 1.8.3 <http://underscorejs.org/LICENSE>
   * Copyright Jeremy Ashkenas, DocumentCloud and Investigative Reporters & Editors
   */ /** Used as references for various `Number` constants. */var MAX_SAFE_INTEGER=9007199254740991;/** `Object#toString` result references. */var argsTag='[object Arguments]',funcTag='[object Function]',genTag='[object GeneratorFunction]';/** Used to detect unsigned integer values. */var reIsUint=/^(?:0|[1-9]\d*)$/;/**
   * A specialized version of `_.map` for arrays without support for iteratee
   * shorthands.
   *
   * @private
   * @param {Array} [array] The array to iterate over.
   * @param {Function} iteratee The function invoked per iteration.
   * @returns {Array} Returns the new mapped array.
   */function arrayMap$1(array,iteratee){var index=-1,length=array?array.length:0,result=Array(length);while(++index<length){result[index]=iteratee(array[index],index,array);}return result;}/**
   * The base implementation of `_.times` without support for iteratee shorthands
   * or max array length checks.
   *
   * @private
   * @param {number} n The number of times to invoke `iteratee`.
   * @param {Function} iteratee The function invoked per iteration.
   * @returns {Array} Returns the array of results.
   */function baseTimes(n,iteratee){var index=-1,result=Array(n);while(++index<n){result[index]=iteratee(index);}return result;}/**
   * The base implementation of `_.values` and `_.valuesIn` which creates an
   * array of `object` property values corresponding to the property names
   * of `props`.
   *
   * @private
   * @param {Object} object The object to query.
   * @param {Array} props The property names to get values for.
   * @returns {Object} Returns the array of property values.
   */function baseValues(object,props){return arrayMap$1(props,function(key){return object[key];});}/**
   * Creates a unary function that invokes `func` with its argument transformed.
   *
   * @private
   * @param {Function} func The function to wrap.
   * @param {Function} transform The argument transform.
   * @returns {Function} Returns the new function.
   */function overArg(func,transform){return function(arg){return func(transform(arg));};}/** Used for built-in method references. */var objectProto$2=Object.prototype;/** Used to check objects for own properties. */var hasOwnProperty$2=objectProto$2.hasOwnProperty;/**
   * Used to resolve the
   * [`toStringTag`](http://ecma-international.org/ecma-262/7.0/#sec-object.prototype.tostring)
   * of values.
   */var objectToString$2=objectProto$2.toString;/** Built-in value references. */var propertyIsEnumerable=objectProto$2.propertyIsEnumerable;/* Built-in method references for those with the same name as other `lodash` methods. */var nativeKeys=overArg(Object.keys,Object);/**
   * Creates an array of the enumerable property names of the array-like `value`.
   *
   * @private
   * @param {*} value The value to query.
   * @param {boolean} inherited Specify returning inherited property names.
   * @returns {Array} Returns the array of property names.
   */function arrayLikeKeys(value,inherited){// Safari 8.1 makes `arguments.callee` enumerable in strict mode.
// Safari 9 makes `arguments.length` enumerable in strict mode.
var result=isArray$1(value)||isArguments(value)?baseTimes(value.length,String):[];var length=result.length,skipIndexes=!!length;for(var key in value){if((inherited||hasOwnProperty$2.call(value,key))&&!(skipIndexes&&(key=='length'||isIndex(key,length)))){result.push(key);}}return result;}/**
   * The base implementation of `_.keys` which doesn't treat sparse arrays as dense.
   *
   * @private
   * @param {Object} object The object to query.
   * @returns {Array} Returns the array of property names.
   */function baseKeys(object){if(!isPrototype(object)){return nativeKeys(object);}var result=[];for(var key in Object(object)){if(hasOwnProperty$2.call(object,key)&&key!='constructor'){result.push(key);}}return result;}/**
   * Checks if `value` is a valid array-like index.
   *
   * @private
   * @param {*} value The value to check.
   * @param {number} [length=MAX_SAFE_INTEGER] The upper bounds of a valid index.
   * @returns {boolean} Returns `true` if `value` is a valid index, else `false`.
   */function isIndex(value,length){length=length==null?MAX_SAFE_INTEGER:length;return!!length&&(typeof value=='number'||reIsUint.test(value))&&value>-1&&value%1==0&&value<length;}/**
   * Checks if `value` is likely a prototype object.
   *
   * @private
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a prototype, else `false`.
   */function isPrototype(value){var Ctor=value&&value.constructor,proto=typeof Ctor=='function'&&Ctor.prototype||objectProto$2;return value===proto;}/**
   * Checks if `value` is likely an `arguments` object.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an `arguments` object,
   *  else `false`.
   * @example
   *
   * _.isArguments(function() { return arguments; }());
   * // => true
   *
   * _.isArguments([1, 2, 3]);
   * // => false
   */function isArguments(value){// Safari 8.1 makes `arguments.callee` enumerable in strict mode.
return isArrayLikeObject(value)&&hasOwnProperty$2.call(value,'callee')&&(!propertyIsEnumerable.call(value,'callee')||objectToString$2.call(value)==argsTag);}/**
   * Checks if `value` is classified as an `Array` object.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an array, else `false`.
   * @example
   *
   * _.isArray([1, 2, 3]);
   * // => true
   *
   * _.isArray(document.body.children);
   * // => false
   *
   * _.isArray('abc');
   * // => false
   *
   * _.isArray(_.noop);
   * // => false
   */var isArray$1=Array.isArray;/**
   * Checks if `value` is array-like. A value is considered array-like if it's
   * not a function and has a `value.length` that's an integer greater than or
   * equal to `0` and less than or equal to `Number.MAX_SAFE_INTEGER`.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is array-like, else `false`.
   * @example
   *
   * _.isArrayLike([1, 2, 3]);
   * // => true
   *
   * _.isArrayLike(document.body.children);
   * // => true
   *
   * _.isArrayLike('abc');
   * // => true
   *
   * _.isArrayLike(_.noop);
   * // => false
   */function isArrayLike(value){return value!=null&&isLength(value.length)&&!isFunction(value);}/**
   * This method is like `_.isArrayLike` except that it also checks if `value`
   * is an object.
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an array-like object,
   *  else `false`.
   * @example
   *
   * _.isArrayLikeObject([1, 2, 3]);
   * // => true
   *
   * _.isArrayLikeObject(document.body.children);
   * // => true
   *
   * _.isArrayLikeObject('abc');
   * // => false
   *
   * _.isArrayLikeObject(_.noop);
   * // => false
   */function isArrayLikeObject(value){return isObjectLike$2(value)&&isArrayLike(value);}/**
   * Checks if `value` is classified as a `Function` object.
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a function, else `false`.
   * @example
   *
   * _.isFunction(_);
   * // => true
   *
   * _.isFunction(/abc/);
   * // => false
   */function isFunction(value){// The use of `Object#toString` avoids issues with the `typeof` operator
// in Safari 8-9 which returns 'object' for typed array and other constructors.
var tag=isObject$3(value)?objectToString$2.call(value):'';return tag==funcTag||tag==genTag;}/**
   * Checks if `value` is a valid array-like length.
   *
   * **Note:** This method is loosely based on
   * [`ToLength`](http://ecma-international.org/ecma-262/7.0/#sec-tolength).
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is a valid length, else `false`.
   * @example
   *
   * _.isLength(3);
   * // => true
   *
   * _.isLength(Number.MIN_VALUE);
   * // => false
   *
   * _.isLength(Infinity);
   * // => false
   *
   * _.isLength('3');
   * // => false
   */function isLength(value){return typeof value=='number'&&value>-1&&value%1==0&&value<=MAX_SAFE_INTEGER;}/**
   * Checks if `value` is the
   * [language type](http://www.ecma-international.org/ecma-262/7.0/#sec-ecmascript-language-types)
   * of `Object`. (e.g. arrays, functions, objects, regexes, `new Number(0)`, and `new String('')`)
   *
   * @static
   * @memberOf _
   * @since 0.1.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is an object, else `false`.
   * @example
   *
   * _.isObject({});
   * // => true
   *
   * _.isObject([1, 2, 3]);
   * // => true
   *
   * _.isObject(_.noop);
   * // => true
   *
   * _.isObject(null);
   * // => false
   */function isObject$3(value){var type=_typeof2(value);return!!value&&(type=='object'||type=='function');}/**
   * Checks if `value` is object-like. A value is object-like if it's not `null`
   * and has a `typeof` result of "object".
   *
   * @static
   * @memberOf _
   * @since 4.0.0
   * @category Lang
   * @param {*} value The value to check.
   * @returns {boolean} Returns `true` if `value` is object-like, else `false`.
   * @example
   *
   * _.isObjectLike({});
   * // => true
   *
   * _.isObjectLike([1, 2, 3]);
   * // => true
   *
   * _.isObjectLike(_.noop);
   * // => false
   *
   * _.isObjectLike(null);
   * // => false
   */function isObjectLike$2(value){return!!value&&_typeof2(value)=='object';}/**
   * Creates an array of the own enumerable property names of `object`.
   *
   * **Note:** Non-object values are coerced to objects. See the
   * [ES spec](http://ecma-international.org/ecma-262/7.0/#sec-object.keys)
   * for more details.
   *
   * @static
   * @since 0.1.0
   * @memberOf _
   * @category Object
   * @param {Object} object The object to query.
   * @returns {Array} Returns the array of property names.
   * @example
   *
   * function Foo() {
   *   this.a = 1;
   *   this.b = 2;
   * }
   *
   * Foo.prototype.c = 3;
   *
   * _.keys(new Foo);
   * // => ['a', 'b'] (iteration order is not guaranteed)
   *
   * _.keys('hi');
   * // => ['0', '1']
   */function keys(object){return isArrayLike(object)?arrayLikeKeys(object):baseKeys(object);}/**
   * Creates an array of the own enumerable string keyed property values of `object`.
   *
   * **Note:** Non-object values are coerced to objects.
   *
   * @static
   * @since 0.1.0
   * @memberOf _
   * @category Object
   * @param {Object} object The object to query.
   * @returns {Array} Returns the array of property values.
   * @example
   *
   * function Foo() {
   *   this.a = 1;
   *   this.b = 2;
   * }
   *
   * Foo.prototype.c = 3;
   *
   * _.values(new Foo);
   * // => [1, 2] (iteration order is not guaranteed)
   *
   * _.values('hi');
   * // => ['h', 'i']
   */function values(object){return object?baseValues(object,keys(object)):[];}var lodash_values=values;var isMimetype=function isMimetype(str){return str.indexOf('/')!==-1;};var matchesMimetype=function matchesMimetype(fileDefinition,singleAcceptOption){if(fileDefinition.mimetype&&singleAcceptOption==='image/*'){return fileDefinition.mimetype.indexOf('image/')!==-1;}if(fileDefinition.mimetype&&singleAcceptOption==='video/*'){return fileDefinition.mimetype.indexOf('video/')!==-1;}if(fileDefinition.mimetype&&singleAcceptOption==='audio/*'){return fileDefinition.mimetype.indexOf('audio/')!==-1;}if(fileDefinition.mimetype&&singleAcceptOption==='application/*'){return fileDefinition.mimetype.indexOf('application/')!==-1;}if(fileDefinition.mimetype&&singleAcceptOption==='text/*'){return fileDefinition.mimetype.indexOf('text/')!==-1;}return fileDefinition.mimetype===singleAcceptOption;};var extractExtension=function extractExtension(filename){var match=/\.\w+$/.exec(filename);return match&&match.length&&match[0];};var normalizeExtension=function normalizeExtension(ext){return ext.replace('.','').toLowerCase();};var matchesExtension=function matchesExtension(fileDefinition,singleAcceptOption){var ext=extractExtension(fileDefinition.name)||'';var fileExt=normalizeExtension(ext);var acceptExt=normalizeExtension(singleAcceptOption);return fileExt===acceptExt;};var canAcceptThisFile=function canAcceptThisFile(fileDefinition,accept){if(accept===undefined){return true;}return accept.some(function(singleAcceptOption){if(isMimetype(singleAcceptOption)){return matchesMimetype(fileDefinition,singleAcceptOption);}return matchesExtension(fileDefinition,singleAcceptOption);});};// All data passed to outside world via callbacks HAVE TO be processed by one
// of those functions. This ensures no internal data will leak outside, and
// that all objects are cloned so users can't cause our app to crash by
// changing some fields in object passed to them.
var convertFileForOutsideWorld=function convertFileForOutsideWorld(f,_ref2){var exposeOriginalFile=_ref2.exposeOriginalFile;var file={filename:f.name,handle:f.handle,mimetype:f.mimetype||f.type,originalPath:f.originalPath||f.path,size:f.size,source:f.source,url:f.url,uploadId:f.uploadId};if(f.originalFile){if(exposeOriginalFile){file.originalFile=f.originalFile;}else{file.originalFile={name:f.originalFile.name,type:f.originalFile.type,size:f.originalFile.size};}}if(f.status)file.status=f.status;if(f.key)file.key=f.key;if(f.container)file.container=f.container;if(f.cropData)file.cropped=JSON.parse(JSON.stringify(f.cropData));if(f.rotated)file.rotated=JSON.parse(JSON.stringify(f.rotated));if(f.workflows)file.workflows=JSON.parse(JSON.stringify(f.workflows));return file;};var convertFileListForOutsideWorld=function convertFileListForOutsideWorld(list,getters){return list.map(function(file){return convertFileForOutsideWorld(file,getters);});};var readableSize=function readableSize(bytes){if(bytes===0){return'0.00B';}var e=Math.floor(Math.log(bytes)/Math.log(1024));return"".concat((bytes/Math.pow(1024,e)).toFixed(2)," ").concat(' KMGTP'.charAt(e),"B");};var displayName=function displayName(normalizedFile){if(normalizedFile.name.length<45){return normalizedFile.name;}var fileSplit=normalizedFile.name.split('.');if(fileSplit.length===2){var truncName="".concat(fileSplit[0].substring(0,42),"..");var fileExt=fileSplit[1];return"".concat(truncName,".").concat(fileExt);}if(fileSplit.length>2){var _truncName="".concat(normalizedFile.name.substring(0,42),"..");var _fileExt=fileSplit[fileSplit.length-1];return"".concat(_truncName,".").concat(_fileExt);}return"".concat(normalizedFile.name.substring(0,42),"...");};var ar={// Actions
Upload:'تحميل','Upload more':'تحميل المزيد','Deselect All':'إلغاء تحديد الكل','View/Edit Selected':'شاهد / قم بتحرير ما تم اختياره','Sign Out':'تسجيل الخروج',// Source Labels
'My Device':'جهازي','Web Search':'البحث في شبكة الويب','Take Photo':'إلتقط صورة','Link (URL)':'رابط عنوان الموقع الالكتروني','Record Video':'تسجيل فيديو','Record Audio':'تسجيل صوت',// Custom Source
'Custom Source':'مصدر مخصص',// Footer Text
Add:'قم بإضافة','more file':'المزيد من الملفات','more files':'ملفات أخرى',// Cloud
'Connect {providerName}':'قم بتوصيل {providerName}','Select Files from {providerName}':'حدد الملفات من {providerName}','You need to authenticate with {providerName}.':'تحتاج إلى المصادقة مع {providerName}.','A new page will open to connect your account.':'.سيتم فتح صفحة جديدة للإتصال بحسابك','We only extract images and never modify or delete them.':'.نقوم فقط بإستخراج الصور ولن نقوم بالتعديل أو الحذف','To disconnect from {providerName} click "Sign out" button in the menu.':'لقطع الاتصال بـ {providerName} ، انقر فوق الزر "تسجيل الخروج" في القائمة.','Sign in with Google':'الدخول مع جوجل','Go back':'عد','This folder is empty.':'Dieser Ordner ist leer.',// Summary
Files:'ملفات',Images:'صور',Uploaded:'تم الرفع / التحميل',Uploading:'يتم الرفع / التحميل',Completed:'تمت العملية',Filter:'قم بتصفية','Cropped Images':'الصور المقصوصة','Edited Images':'الصور المحررة','Selected Files':'الملفات المختارة','Crop is required on images':'مطلوب المحاصيل على الصور',// Transform
Crop:'قم بإقتصاص',Circle:'قم بإقتصاص الصورة بشكل دائري / قطع دائري',Rotate:'قم بتدوير',Mask:'قم بحجب',Revert:'تراجع',Edit:'قم بتحرير',Reset:'إعد ضبط',Done:'إانتهى',Save:'حفظ',Next:'التالى','Edit Image':'قم بتحرير الصورة','This image cannot be edited':'لا يمكن تحرير هذه الصورة',// Retry messaging
'Connection Lost':'تم قطع الإتصال','Failed While Uploading':'حصل خطأ أثناء التحميل','Retrying in':'سيتم إعادة المحاولة بعد','Try again':'حاول مرة أخرى','Try now':'أعد المحاولة',// Local File Source
'Drag and Drop, Copy and Paste Files':'سحب وإسقاط الملفات ، نسخ ولصق الملفات','or Drag and Drop, Copy and Paste Files':'أو إسحب و أترك، إنسخ و ألصق الملفات','Select Files to Upload':'اختر الملفات للتحميل','Select From':'إختر من','Drop your files anywhere':'إسقاط الملفات الخاصة بك في أي مكان',// Input placeholders
'Enter a URL':'URL أدخل','Search images':'إبحث عن الصور',// Webcam Source
'Webcam Disabled':'كاميرا الويب معطلة','Webcam Not Supported':'كاميرا الويب غير مدعمة','Please enable your webcam to take a photo.':'الرجاء تفيل كاميرا الويب لأخذ صورة','Your current browser does not support webcam functionality.':'متصفح الويب المستخدم حاليا لا يدعم خاصية كاميرا الويب','We suggest using Chrome or Firefox.':'Firefox أو Chrome نقترح استخدام متصفح',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'{types} ليس من أنواع الملفات المقبولة ، أنواع الملفات المقبولة هي {displayName} ملف','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{roundFileSize} أكبر من الحد المسموح ، الحد المسموح أقل من{displayName} الملف','Our file upload limit is {maxFiles} {filesText}':'{maxFiles} {filesText} الحد الأعلى المسموح به لتحميل الملفات هو','No search results found for "{search}"':'لم يتم العثور على نتائج بحث لـ "{search}"','An error occurred. Please try again.':'حدث خطأ. حاول مرة اخرى.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'الملفات [{displayName}] كبيرة جدًا. حجم الملف المقبول هو {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'انقر هنا أو اضغط على ESC للإغلاق'};var ca={// Actions
Upload:'Carrega','Upload more':'Carregar mais','Deselect All':'Desfés tota la selecció','View/Edit Selected':'Visualitza/Edita les seleccionades','Sign Out':'Surt',// Source Labels
'My Device':'El meu dispositiu','Web Search':'Cerca al web','Take Photo':'Fes una foto','Link (URL)':'URL','Record Video':'Grava vídeo','Record Audio':'Grava l\'àudio',// Custom Source
'Custom Source':'Origen personalitzada',// Footer Text
Add:'Afegeix','more file':'més arxiu','more files':'més arxius',// Cloud
'Connect {providerName}':'Connecteu {providerName}','Select Files from {providerName}':'Seleccioneu Fitxers de {providerName}','You need to authenticate with {providerName}.':'Heu d’autenticar-vos amb {providerName}.','A new page will open to connect your account.':'S\'obrirà una nova pàgina per a connectar-te al teu compte','We only extract images and never modify or delete them.':'Tan sols agafem les imatges, però mai les modifiquem o eliminem.','To disconnect from {providerName} click "Sign out" button in the menu.':'Per desconnectar-vos de {providerName}, feu clic al botó "Surt" al menú.','Sign in with Google':'Inicieu la sessió amb Google','Go back':'Torna','This folder is empty.':'Dieser Ordner ist leer.',// Summary
Files:'Arxius',Images:'Imatges',Uploaded:'Carregada',Uploading:'Carregant',Completed:'Finalitzat',Filter:'Filtra','Cropped Images':'Imatges escapçades','Edited Images':'Imatges editades','Selected Files':'Arxius seleccionats','Crop is required on images':'Es requereix un cultiu a les imatges',// Transform
Crop:'Escapça',Circle:'Cercle',Rotate:'Gira',Mask:'Emmascara',Revert:'Reverteix',Edit:'Edita',Reset:'Reinicia',Done:'Fet',Save:'Guardar',Next:'Pròxim','Edit Image':'Edita la imatge','This image cannot be edited':'Aquesta imatge no es pot editar',// Retry messaging
'Connection Lost':'S\'ha perdut la connexió','Failed While Uploading':'Error de càrrega','Retrying in':'Reintentant en','Try again':'Torna-ho a intentar','Try now':'Prova-ho ara',// Local File Source
'Drag and Drop, Copy and Paste Files':'Arrossegueu i deixeu anar els fitxers, copieu i enganxeu fitxers','or Drag and Drop, Copy and Paste Files':'o arrosega, copia i enganxa els arxius','Select Files to Upload':'Selecciona els arxius a carregar','Select From':'Selecciona de','Drop your files anywhere':'Deixeu anar els fitxers en qualsevol lloc',// Input placeholders
'Enter a URL':'Introduïu un URL','Search images':'Cerca imatges',// Webcam Source
'Webcam Disabled':'Webcam inhabilitada','Webcam Not Supported':'Webcam no admesa','Please enable your webcam to take a photo.':'Sisplau, habilita la webcam per a fer la foto','Your current browser does not support webcam functionality.':'El teu navegador no admet la funcionalitat de webcam','We suggest using Chrome or Firefox.':'Recomanem utilitzar Chrome o Firefox.',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'L\'arxiu {displayName} no té un format vàlid. Els formats acceptats són {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'L\'arxiu {displayName} és massa gran. El tamany màxim acceptat és {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'El límit de càrrega és {maxFiles} {filesText}','No search results found for "{search}"':'No s’han trobat resultats per a "{search}"','An error occurred. Please try again.':'Hi ha hagut un error. Siusplau torna-ho a provar.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Els fitxers [{displayName}] són massa grans. La mida del fitxer acceptada és {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Feu clic aquí o premeu ESC per tancar'};var da={// Actions
Upload:'Upload','Upload more':'Upload flere','Deselect All':'Fravælg alle','View/Edit Selected':'Vis/rediger valgte','Sign Out':'Log ud',// Source Labels
'My Device':'Min enhed','Web Search':'Søgning på internettet','Take Photo':'Tag billede','Link (URL)':'Link (URL)','Record Video':'Optag video','Record Audio':'Optag lyd',// Custom Source
'Custom Source':'Brugerdefineret Kilde',// Footer Text
Add:'Tilføj','more file':'fil mere','more files':'flere filer',// Cloud
'Connect {providerName}':'Tilslut {providerName}','Select Files from {providerName}':'Vælg filer fra {providerName}','You need to authenticate with {providerName}.':'Du skal godkende med {providerName}.','A new page will open to connect your account.':'En ny side vil åbne for at forbinde med din konto','We only extract images and never modify or delete them.':'Vi hiver kun billeder og modificerer eller sletter dem aldrig','To disconnect from {providerName} click "Sign out" button in the menu.':'For at afbryde forbindelsen fra {providerName} skal du klikke på knappen "Log ud" i menuen.','Sign in with Google':'Log ind med Google','Go back':'Gå tilbage','This folder is empty.':'Dieser Ordner ist leer.',// Summary
Files:'Filer',Images:'Billeder',Uploaded:'Uploaded',Uploading:'Uploader',Completed:'Fuldført',Filter:'Filtrer','Cropped Images':'Beskærede billeder','Edited Images':'Redigerede filer','Selected Files':'Valgte filer','Crop is required on images':'Beskæring er påkrævet',// Transform
Crop:'Klippe',Circle:'Cirkel',Rotate:'Rotere',Mask:'Form',Revert:'Gør om',Edit:'Rediger',Reset:'Nulstil',Done:'Færdig',Save:'Gemme',Next:'Næste','Edit Image':'Rediger billede','This image cannot be edited':'',// Retry messaging
'Connection Lost':'Forbindelse tabt','Failed While Uploading':'Mislykkedes under upload','Retrying in':'Prøver igen om','Try again':'Prøv igen','Try now':'Prøv nu',// Local File Source
'Drag and Drop, Copy and Paste Files':'Træk og slip filer, kopier og indsæt filer','or Drag and Drop, Copy and Paste Files':'Eller træk og slip, kopier og indsæt filer','Select Files to Upload':'Vælg filer til upload','Select From':'Vælg fra','Drop your files anywhere':'Drop dine filer overalt',// Input placeholders
'Enter a URL':'Skriv en webadresse','Search images':'Søg billeder',// Webcam Source
'Webcam Disabled':'Webkamera deaktiveret','Webcam Not Supported':'Webkamera understøttes ikke','Please enable your webcam to take a photo.':'Aktivér dit webcam for at tage et billede','Your current browser does not support webcam functionality.':'Din nuværende browser understøtter ikke webcam','We suggest using Chrome or Firefox.':'Vi foreslår at bruge Chrome eller Firefox',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'Filen {displayName} er ikke i et acceptabelt format. De accepterede formater er {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':' Filen {displayName} er for stor. Den accepterede filstørrelse er {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':' Vores filstørrelse er begrænset til {maxFiles} {filesText}','No search results found for "{search}"':'Ingen søgeresultater fundet for "{search}"','An error occurred. Please try again.':'En fejl opstod. Prøv igen.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Filer [{displayName}] er for store. Den accepterede filstørrelse er {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Klik her eller tryk ESC for at lukke'};var de={// Actions
Upload:'Hochladen','Upload more':'Mehr hochladen','Deselect All':'Deaktivieren Sie alle','View/Edit Selected':'Anzeigen/Bearbeiten ausgewählt','Sign Out':'Abmelden',// Source Labels
'My Device':'Mein Gerät','Web Search':'Internetsuche','Take Photo':'Foto machen','Link (URL)':'URL-Adresse','Record Video':'Ein Video aufnehmen','Record Audio':'Ton aufnehmen',// Custom Source
'Custom Source':'Benutzerdefinierte Quelle',// Footer Text
Add:'Hinzufügen','more file':'weitere Datei','more files':'weitere Dateien',// Cloud
'Connect {providerName}':'Verbinden mit {providerName}','Select Files from {providerName}':'Wählen Sie Dateien aus {providerName}','You need to authenticate with {providerName}.':'Sie müssen sich mit {providerName} anmelden','A new page will open to connect your account.':'Eine neue Seite wird geöffnet, um Ihr Konto zu verbinden','We only extract images and never modify or delete them.':'Wir extrahieren Bilder nur und modifizieren oder löschen sie niemals','To disconnect from {providerName} click "Sign out" button in the menu.':'Um die Verbindung zu {providerName} zu trennen, klicken Sie im Menü auf "Abmelden".','Sign in with Google':'Anmeldung mit Google','Go back':'Zurück','This folder is empty.':'Dieser Ordner ist leer.',// Summary
Files:'Dateien',Images:'Bilder',Uploaded:'Hochgeladen',Uploading:'Wird hochgeladen',Completed:'Abgeschlossen',Filter:'Filtern','Cropped Images':'Freigestellte Bilder','Edited Images':'Bearbeitete Bilder','Selected Files':'Ausgewählten Dateien','Crop is required on images':'Zuschneiden ist für Bilder erforderlich',// Transform
Crop:'Zuschneiden',Circle:'Kreis',Rotate:'Rotieren',Mask:'Verdecken',Revert:'Rückgängig',Edit:'Bearbeiten',Reset:'Zurück',Done:'Fertig',Save:'Speichern',Next:'Nächster','Edit Image':'Bild bearbeiten','This image cannot be edited':'Dieses Bild kann nicht bearbeitet werden',// Retry messaging
'Connection Lost':'Keine Verbindung','Failed While Uploading':'Beim Hochladen fehlgeschlagen','Retrying in':'Wiederholen in','Try again':'Versuch es noch einmal','Try now':'Versuche es jetzt',// Local File Source
'Drag and Drop, Copy and Paste Files':'Dateien ziehen und ablegen, Dateien kopieren und einfügen','or Drag and Drop, Copy and Paste Files':'oder per Drag&Drop einfügen','Select Files to Upload':'Datei auswählen','Select From':'Wählen Sie aus','Drop your files anywhere':'Legen Sie Ihre Dateien überall ab',// Input placeholders
'Enter a URL':'Geben Sie eine URL ein','Search images':'Suche bilder',// Webcam Source
'Webcam Disabled':'Webcam ausgeschaltet','Webcam Not Supported':'Webcam nicht unterstützt','Please enable your webcam to take a photo.':'Bitte aktivieren Sie Ihre Webcam, um ein Foto zu machen','Your current browser does not support webcam functionality.':'Ihr aktueller Browser unterstützt keine Webcam-Funktionen.','We suggest using Chrome or Firefox.':'Wir empfehlen, mit Chrome oder Firefox.',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'Datei-{displayName} ist keine anerkannte Dateityp. Die akzeptierten Dateitypen sind {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{displayName} Datei ist zu groß. Die akzeptierten Dateigröße beträgt {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Unser Dateigrößenlimit ist {maxFiles} {filesText}','No search results found for "{search}"':'Keine Suchergebnisse für "{search}" gefunden','An error occurred. Please try again.':'Ein Fehler ist aufgetreten. Bitte versuche es erneut.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Dateien [{displayName}] sind zu groß. Die akzeptierte Dateigröße ist {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Klicken Sie hier um zurückzukehren oder drücken Sie Esc'};var en={'File {displayName} is not an accepted file type. The accepted file types are {types}':'File {displayName} is not an accepted file type. The accepted file types are {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'File {displayName} is too big. The accepted file size is less than {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Our file upload limit is {maxFiles} {filesText}','No search results found for "{search}"':'No search results found for "{search}"','An error occurred. Please try again.':'An error occurred. Please try again.'};var es={// Actions
Upload:'Subir','Upload more':'Subir más','Deselect All':'Deseleccionar Todo','View/Edit Selected':'Ver/Editar Seleccionado','Sign Out':'Desconectar',// Source Labels
'My Device':'Mi Dispositivo','Web Search':'Búsqueda de Internet','Take Photo':'Tomar la foto','Link (URL)':'Dirección URL','Record Video':'Grabar video','Record Audio':'Grabar audio',// Custom Source
'Custom Source':'Fuente personalizada',// Footer Text
Add:'Añadir','more file':'el archivo más','more files':'el archivo más',// Cloud
'Connect {providerName}':'Conectar {providerName}','Select Files from {providerName}':'Seleccione Archivos de {providerName}','You need to authenticate with {providerName}.':'Necesitas autenticarte con {providerName}.','A new page will open to connect your account.':'Se abrirá una nueva página para conectar tu cuenta.','We only extract images and never modify or delete them.':'Sólo extraemos imágenes y nunca las modificamos o eliminamos','To disconnect from {providerName} click "Sign out" button in the menu.':'Desconectarse de Instagram, haga clic en el botón "Desconectar" en el menú.','Sign in with Google':'Inicia sesión con Google','Go back':'Volver','This folder is empty.':'Dieser Ordner ist leer.',// Summary
Files:'Archivos',Images:'Imágenes',Uploaded:'Subido',Uploading:'Subiendo',Completed:'Completado',Filter:'Filtrar','Cropped Images':'Imágenes recortadas','Edited Images':'Imágenes editadas','Selected Files':'Archivos seleccionados','Crop is required on images':'Se requiere cultivo en las imágenes',// Transform
Crop:'Recortar',Circle:'Circulo',Rotate:'Rotar',Mask:'Encubrir',Revert:'Deshacer',Edit:'Editar',Reset:'Restablecer',Done:'Terminado',Save:'Guardar',Next:'Siguiente','Edit Image':'Editar imagen','This image cannot be edited':'Esta imagen no puede ser editada',// Retry messaging
'Connection Lost':'Se ha perdido la conexión','Failed While Uploading':'Error durante la subida','Retrying in':'Volver a intentar en','Try again':'Inténtalo de nuevo','Try now':'Probar ahora',// Local File Source
'Drag and Drop, Copy and Paste Files':'Arrastrar y soltar archivos, copiar y pegar archivos','or Drag and Drop, Copy and Paste Files':'O arrastra y suéltalos, o copia y pégalos','Select Files to Upload':'Selecciona los archivos a subir','Select From':'Seleccione de','Drop your files anywhere':'Deja tus archivos en cualquier lugar',// Input placeholders
'Enter a URL':'Ingresa una URL','Search images':'Búsqueda de imágenes',// Webcam Source
'Webcam Disabled':'Webcam deshabilitada','Webcam Not Supported':'Webcam no soportada','Please enable your webcam to take a photo.':'Por favor, habilite su webcam para tomar una foto','Your current browser does not support webcam functionality.':'Su navegador actual no admite la funcionalidad de webcam','We suggest using Chrome or Firefox.':'Sugerimos usar Chrome o Firefox',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'Archivo {displayName} no es un tipo de archivo aceptado. Los tipos de archivo aceptados son {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{displayName} De archivo es demasiado grande. El tamaño aceptado es {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Nuestro límite de upload de archivo es {maxFiles} {filesText}','No search results found for "{search}"':'No se han encontrado resultados de búsqueda para "{search}"','An error occurred. Please try again.':'Ocurrió un error. Inténtalo de nuevo.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Los archivos [{displayName}] son demasiado grandes. El tamaño de archivo aceptado es {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Presiona aquí o la tecla ESC para cerrar'};var fr={// Actions
Upload:'Ajouter','Upload more':'Ajouter plus','Deselect All':'Tout déselectionner','View/Edit Selected':'AVoir/Modifier la sélection','Sign Out':'Se déconnecter',// Source Labels
'My Device':'Mon appareil','Web Search':'Recherche Internet','Take Photo':'Prendre une Photo','Link (URL)':'Adresse URL','Record Video':'Enregistrer une vidéo','Record Audio':'Enregistrement audio',// Custom Source
'Custom Source':'Source personnalisée',// Footer Text
Add:'Ajouter','more file':'plus de fichier','more files':'plus de fichiers',// Cloud
'Connect {providerName}':'Se connecter avec {providerName}','Select Files from {providerName}':'Sélectionner des fichiers dans {providerName}','You need to authenticate with {providerName}.':'Vous devez vous authentifier avec {providerName}.','A new page will open to connect your account.':"Une nouvelle page s'ouvrira pour connecter votre compte.",'We only extract images and never modify or delete them.':'Nous utilisons les images sans les modifier, ni les supprimer','To disconnect from {providerName} click "Sign out" button in the menu.':'Pour vous déconnecter {providerName}, cliquez sur le bouton "Se déconnecter" du menu.','Sign in with Google':'Connectez-vous avec Google','Go back':'Retourner','This folder is empty.':'Dieser Ordner ist leer.',// Summary
Files:'Fichiers',Images:'Images',Uploaded:'Ajouté',Uploading:'Ajouté',Completed:'Terminé',Filter:'Filtrer','Cropped Images':'Images Rognées','Edited Images':'Images Éditées','Selected Files':'Fichiers sélectionnés','Crop is required on images':'La culture est requise sur les images',// Transform
Crop:'Rogner',Circle:'Rond',Rotate:'Pivoter',Mask:'Cache',Revert:'Annuler',Edit:'Modifier',Reset:'Annuler',Done:'Fini',Save:'Appliquer',Next:'Prochain','Edit Image':'Image Éditer','This image cannot be edited':'Cette image ne peut pas être modifiée',// Retry messaging
'Connection Lost':'Connexion perdue','Failed While Uploading':'Échec du chargement','Retrying in':'Réessayer dans','Try again':'Réessayer','Try now':'Essayez maintenant',// Local File Source
'Drag and Drop, Copy and Paste Files':'Glisser et déposer des fichiers, copier et coller des fichiers','or Drag and Drop, Copy and Paste Files':'ou faites glisser, copier et coller des fichiers','Select Files to Upload':'Sélectionnez des fichiers à ajouter','Select From':'Sélectionnez depuis','Drop your files anywhere':'Déposez vos fichiers n\'importe où',// Input placeholders
'Enter a URL':'Entrez une URL','Search images':'Rechercher des images',// Webcam Source
'Webcam Disabled':'Webcam désactivé','Webcam Not Supported':'Webcam non prise en charge','Please enable your webcam to take a photo.':"S'il vous plaît activer votre webcam pour prendre une photo",'Your current browser does not support webcam functionality.':'Votre navigateur actuel ne prend pas en charge la fonctionnalité webcam','We suggest using Chrome or Firefox.':"Nous vous suggérons d'utiliser Chrome ou Firefox.",// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'{displayName} De fichier n’est pas un type de fichier accepté. Les types de fichiers acceptés sont {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'Le fichier {displayName} est trop grand. La taille de fichier acceptée est {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Notre limite de téléchargement de fichier est {maxFiles} {filesText}','No search results found for "{search}"':'Aucun résultat de recherche trouvé pour "{search}"','An error occurred. Please try again.':'Une erreur est survenue. Veuillez réessayer.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Les fichiers [{displayName}] sont trop gros. La taille du fichier accepté est {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Cliquez ici ou appuyez sur ESC pour fermer'};var he={// Actions
Upload:'העלה','Upload more':'העלה עוד','Deselect All':'בטל בחירה','View/Edit Selected':'צפה/ערוך בחירה','Sign Out':'התנתק',// Source Labels
'My Device':'המכשיר שלי','Web Search':'חיפוש תמונות','Take Photo':'לצלם','Link (URL)':'כתובת אתר','Record Video':'להקליט סרטון','Record Audio':'הקלט שמע',// Custom Source
'Custom Source':'מקור מותאם אישית',// Footer Text
Add:'הוסף','more file':'עוד קובץ','more files':'עוד קבצים',// Cloud
'Connect {providerName}':'חבר {providerName}','Select Files from {providerName}':'בחר קבצים מ- {providerName}','You need to authenticate with {providerName}.':'אתה צריך לאמת עם {providerName}.','A new page will open to connect your account.':'עמוד חדש יפתח על מנת לחבר את חשבונך','We only extract images and never modify or delete them.':'לעולם לא נשנה או נמחק את התמונות','To disconnect from {providerName} click "Sign out" button in the menu.':'כדי להתנתק מ {providerName} לחץ על הלחצן "התנתק" בתפריט.','Sign in with Google':'היכנס באמצעות Google','Go back':'תחזור','This folder is empty.':'Dieser Ordner ist leer.',// Summary
Files:'קבצים',Images:'תמונות',Uploaded:'הועלו',Uploading:'מעלה',Completed:'הושלם',Filter:'סנן','Cropped Images':'תמונות חתוכות','Edited Images':'תמונות ערוכות','Selected Files':'קבצים שנבחרו','Crop is required on images':'חיתוך נדרש בתמונות',// Transform
Crop:'לִגזוֹם',Circle:'חוּג',Rotate:'לְהִסְתוֹבֵב',Mask:'לְכַסוֹת בְּמַסֵכָה',Revert:'בטל שינויים',Edit:'ערוך',Reset:'לְאַתחֵל',Done:'בוצע',Save:'להציל',Next:'הַבָּא','Edit Image':'ערוך תמונה','This image cannot be edited':'לא ניתן לערוך תמונה זו',// Retry messaging
'Connection Lost':'חיבור נעלם','Failed While Uploading':'נכשל בעת העלאה','Retrying in':'מנסה שוב בעוד','Try again':'נסה שוב','Try now':'נסה עכשיו',// Local File Source
'Drag and Drop, Copy and Paste Files':'גרור ושחרר קבצים, העתק והדבק קבצים','or Drag and Drop, Copy and Paste Files':'או גרור / העתק והדבק את הקבצים','Select Files to Upload':'בחר קבצים להעלות','Select From':'בחר מ-','Drop your files anywhere':'זרוק את הקבצים שלך בכל מקום',// Input placeholders
'Enter a URL':'הזן כתובת אתר','Search images':'חיפוש תמונות',// Webcam Source
'Webcam Disabled':'מצלמה מכובת','Webcam Not Supported':'מצלמה לא נתמכת','Please enable your webcam to take a photo.':'נא הדלק את המצלמה על מנת לצלם','Your current browser does not support webcam functionality.':'הדפדפן בו אתה משתמש אינו תומך בשימוש במצלמה','We suggest using Chrome or Firefox.':'אנו ממליצים על דפדפן פיירפוקס או כרום',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'{types} הוא לא מסוג שנוכל לקבל.סוגי הקבצים המותר הם {displayName} הקובץ','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{roundFileSize} גדול מדי. הגודל המותר הו א {displayName} הקובץ','Our file upload limit is {maxFiles} {filesText}':'{filesText} {maxFiles} מגבלת גודל הקובץ היא','No search results found for "{search}"':'לא נמצאו תוצאות חיפוש עבור "{search}"','An error occurred. Please try again.':'התרחשה שגיאה. בבקשה נסה שוב.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'הקבצים [{displayName}] גדולים מדי. גודל הקובץ המקובל הוא {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'לחץ כאן או לחץ על ESC כדי לסגור'};var it={// Actions
Upload:'Caricare','Upload more':'Carica di più','Deselect All':'Deselezionare tutto','View/Edit Selected':'Visualizza/Modifica selezionato','Sign Out':'Esci',// Source Labels
'My Device':'Il mio dispositivo','Web Search':'Ricerca sul Web','Take Photo':'Fare una foto','Link (URL)':'Indirizzo URL','Record Video':'Registra video','Record Audio':'Registra audio',// Custom Source
'Custom Source':'Fonte personalizzata',// Footer Text
Add:'Aggiungere','more file':'più file','more files':'più file',// Cloud
'Connect {providerName}':'Connetti {providerName}','Select Files from {providerName}':'Seleziona File da {providerName}','You need to authenticate with {providerName}.':'Devi autenticarti con {providerName}.','A new page will open to connect your account.':'Si aprirà una nuova pagina per collegare il tuo account','We only extract images and never modify or delete them.':'Abbiamo estratto solo immagini e non modificarli o cancellarli.','To disconnect from {providerName} click "Sign out" button in the menu.':'Per disconnettersi da {providerName} fai clic sul pulsante "Esci" nel menu.','Sign in with Google':'Accedi con Google','Go back':'Torna indietro','This folder is empty.':'Questa cartella è vuota.',// Summary
Files:'File',Images:'Immagini',Uploaded:'Caricato',Uploading:'Caricamento',Completed:'Completato',Filter:'Filtrare','Cropped Images':'Immagini Ritagliate','Edited Images':'Immagini Modificate','Selected Files':'File selezionati','Crop is required on images':'Il ritaglio è richiesto sulle immagini',// Transform
Crop:'Ritaglia',Circle:'Circolo',Rotate:'Ruotare',Mask:'Mascherare',Revert:'Annulla',Edit:'Modifica',Reset:'Reset',Done:'Finito',Save:'Salvare',Next:'Il prossimo','Edit Image':'Modifica Immagine','This image cannot be edited':'Questa immagine non può essere modificata',// Retry messaging
'Connection Lost':'Connessione Persa','Failed While Uploading':'Errore Durante il Caricamento','Retrying in':'Riprovare tra','Try again':'Riprova','Try now':'Prova ora',// Local File Source
'Drag and Drop, Copy and Paste Files':'Trascina e rilascia i file, copia e incolla i file','or Drag and Drop, Copy and Paste Files':'o trascinare, copiare e incollare file','Select Files to Upload':'Selezionare i file da caricare','Select From':'Selezionare da','Drop your files anywhere':'Rilascia i tuoi file ovunque',// Input placeholders
'Enter a URL':'Inserisci un URL','Search images':'Ricerca immagini',// Webcam Source
'Webcam Disabled':'Webcam spenta','Webcam Not Supported':'Webcam non supportato','Please enable your webcam to take a photo.':'Si prega di attivare la webcam per scattare una foto.','Your current browser does not support webcam functionality.':'Il browser corrente non supporta la funzionalità webcam.','We suggest using Chrome or Firefox.':'Suggeriamo usando Chrome o Firefox.',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'{displayName} File non è un tipo di file accettato. I tipi di file accettati sono {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{displayName} Il file è molto grande. La dimensione massima accettata per i file è {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'È il nostro limite di upload di file {maxFiles} {filesText}','No search results found for "{search}"':'Nessun risultato di ricerca trovato per "{search}"','An error occurred. Please try again.':'Si è verificato un errore. Per favore riprova.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'I file [{displayName}] sono troppo grandi. La dimensione del file accettata è {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Clicca qui o premi ESC per chiudere'};var ja={// Actions
Upload:'アップロード','Upload more':'もっとアップロードする','Deselect All':'選択を全て解除','View/Edit Selected':'選択したファイルを閲覧/編集','Sign Out':'サインアウト',// Source Labels
'My Device':'マイデバイス','Web Search':'ウェブ検索','Take Photo':'写真を撮る','Link (URL)':'URLアドレス','Record Video':'録画映像','Record Audio':'レコードオーディオ',// Custom Source
'Custom Source':'カスタムソース',// Footer Text
Add:'追加','more file':'追加ファイル','more files':'追加ファイル',// Cloud
'Connect {providerName}':'{providerName}に接続','Select Files from {providerName}':'{providerName}からファイルを選択','You need to authenticate with {providerName}.':'{providerName}を認証してください','A new page will open to connect your account.':'マイページに移動します','We only extract images and never modify or delete them.':'画像の抽出だけを行い、修正や削除をすることはありません','To disconnect from {providerName} click "Sign out" button in the menu.':'{providerName}を切断するには[サインアウト]ボタンをクリックしてください','Sign in with Google':'Googleでサインイン','Go back':'戻る','This folder is empty.':'このフォルダーは空です。',// Summary
Files:'ファイル',Images:'画像',Uploaded:'アップロード完了',Uploading:'アップロード中',Completed:'完了',Filter:'フィルター','Failed While Uploading':'アップロード中に失敗しました','Cropped Images':'切り抜かれた画像','Edited Images':'編集された画像','Selected Files':'選択済のファイル','Crop is required on images':'画像に作物が必要です','This image cannot be edited':'この画像は編集できません',// Transform
Crop:'剪る',Circle:'円型',Rotate:'廻る',Mask:'覆う',Revert:'元に戻す',Edit:'編集',Reset:'セットをしなおす',Done:'終えた',Save:'適用する',Next:'次','Edit Image':'画像編集',// Retry messaging
'Connection Lost':'接続が切断されました','Retrying in':'以下で再試行：','Try again':'再試行する','Try now':'今すぐやってみて下さい',// Local File Source
'Drag and Drop, Copy and Paste Files':'ファイルのドラッグアンドドロップ、ファイルのコピーと貼り付け','or Drag and Drop, Copy and Paste Files':'または、ファイルをドラッグ・アンド・ドロップ、コピー・アンド・ペーストする','Select Files to Upload':'アップロードするファイルを選択','Select From':'から選択','Drop your files anywhere':'どこにでもファイルをドロップする',// Input placeholders
'Enter a URL':'URLを入力','Search images':'画像を検索',// Webcam Source
'Webcam Disabled':'ウェブカメラはオフ','Webcam Not Supported':'ウェブカメラはサポートされません','Please enable your webcam to take a photo.':'写真を撮るためにウェブカメラを有効にしてください。','Your current browser does not support webcam functionality.':'現在のブラウザはウェブカメラ機能をサポートしていません。','We suggest using Chrome or Firefox.':'私達は firefox を使用することを提案する。',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'ファイル名{displayName}は、送受信可能なファイル形式ではありません。受け入れ可能なファイル形式は{types}です','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'ファイル名{displayName}の容量が大きすぎます。送受信可能なファイルのサイズは{roundFileSize}です','Our file upload limit is {maxFiles} {filesText}':'一度にアップロードできるファイルの上限は {maxFiles} {filesText}','No search results found for "{search}"':'"{search}"に該当するものはありません','An error occurred. Please try again.':'エラーが発生しました。 もう一度やり直してください。','Files [{displayName}] are too big. The accepted file size is {maxSize}':'ファイル[{displayName}]は大きすぎます。 受け入れられるファイルサイズは{maxSize}です',// Other UI labels and titles
'Click here or hit ESC to close picker':'ここをクリックするかESCを押すと閉じます'};var ko={// Actions
Upload:'업로드하기','Upload more':'더 업로드','Deselect All':'모두 취소하기','View/Edit Selected':'선택한 파일들 보기/수정하기','Sign Out':'로그아웃',// Source Labels
'My Device':'내 기기','Web Search':'웹 검색','Take Photo':'사진을 찍어','Link (URL)':'링크','Record Video':'비디오 녹화','Record Audio':'오디오 녹음',Facebook:'페이스북',Instagram:'인스타그램',Dropbox:'드랍박스','Google Photos':'구글 포토','Google Drive':'구글 드라이브',// Custom Source
'Custom Source':'맞춤 소스',// Footer Text
Add:'추가','more file':'다른 파일','more files':'다른 파일들',// Cloud
'Connect {providerName}':'{providerName} 연결','Select Files from {providerName}':'{providerName}에서 파일 선택','You need to authenticate with {providerName}.':'{providerName}로 인증해야합니다.','A new page will open to connect your account.':'계정에 연결하기 위해 새로운 창이 열립니다','We only extract images and never modify or delete them.':'이미지를 수정 또는 삭제하지 않고 불러들입니다','To disconnect from {providerName} click "Sign out" button in the menu.':'{providerName}에서 연결을 끊으려면 메뉴에서 "로그 아웃"버튼을 클릭하십시오.','Sign in with Google':'Google로 로그인','Go back':'돌아 가기','This folder is empty.':'이 폴더는 비어 있습니다.',// Summary
Files:'파일들',Images:'이미지',Uploaded:'업로드되었습니다',Uploading:'업로드 중',Completed:'완료 됨',Filter:'필터','Cropped Images':'자른 이미지','Edited Images':'수정된 파일','Selected Files':'선택된 파일','Crop is required on images':'이미지에 자르기가 필요합니다.',// Transform
Crop:'수확고',Circle:'원',Rotate:'회전하다',Mask:'감추다',Revert:'복원',Edit:'수정',Reset:'다시 놓기',Done:'끝난',Save:'구하다',Next:'다음 것','Edit Image':'이미지 편집','This image cannot be edited':'이 이미지는 편집 할 수 없습니다.',// Retry messaging
'Connection Lost':'연결이 끊어짐','Failed While Uploading':'업로드하는 동안 실패했습니다','Retrying in':'에서 다시 시도','Try again':'다시 시도해보십시오','Try now':'지금 시도',// Local File Source
'Drag and Drop, Copy and Paste Files':'파일 드래그 앤 드롭, 파일 복사 및 붙여 넣기','or Drag and Drop, Copy and Paste Files':'또는 파일을 이곳에 끌어다 놓거나, 복사 붙여넣기를 할 수 있습니다','Select Files to Upload':'클릭해서 업로드할 파일을 선택해주세요','Select From':'선택부터','Drop your files anywhere':'어디서나 파일 놓기',// Input placeholders
'Enter a URL':'URL 입력','Search images':'이미지 검색하기',// Webcam Source
'Webcam Disabled':'웹캠 기능이 비활성화되었습니다','Webcam Not Supported':'웹캠이 지원되지 않는 기기입니다','Please enable your webcam to take a photo.':'사진을 찍으려면 웹캠 기능을 활성화해주세요','Your current browser does not support webcam functionality.':'현재 브라우저는 웹캠 기능을 지원하지 않습니다','We suggest using Chrome or Firefox.':'크롬 또는 파이어폭스를 이용해주세요',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'{displayName}: 업로드 가능한 파일 형식({types})이 아니라 업로드할 수 없습니다','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{displayName}: 업로드 가능한 파일 사이즈({roundFileSize})보다 커서 업로드할 수 없습니다','Our file upload limit is {maxFiles} {filesText}':'최대 {maxFiles}개 파일을 업로드할 수 있습니다 ({filesText})','No search results found for "{search}"':'"{search}"에 대한 검색 결과가 없습니다.','An error occurred. Please try again.':'오류가 발생했습니다. 다시 시도하십시오.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'[{displayName}] 파일이 너무 큽니다. 허용되는 파일 크기는 {maxSize}입니다.',// Other UI labels and titles
'Click here or hit ESC to close picker':'여기를 클릭하거나 Esc 키를 눌러 닫습니다.'};var nl={// Actions
Upload:'Uploaden','Upload more':'Upload meer','Deselect All':'Deselecteer alles','View/Edit Selected':'Selectie bekijken/aanpassen','Sign Out':'Afmelden',// Source Labels
'My Device':'Mijn apparaat','Web Search':'Zoeken op het web','Take Photo':'Foto nemen','Link (URL)':'Link (URL)','Record Video':'Video opnemen','Record Audio':'Geluid opnemen',// Custom Source
'Custom Source':'Aangepaste bron',// Footer Text
Add:'Toevoegen','more file':'meer bestand','more files':'meer bestanden',// Cloud
'Connect {providerName}':'Verbind {providerName}','Select Files from {providerName}':'Selecteer bestanden op {providerName}','You need to authenticate with {providerName}.':'U moet verifiëren met {providerName}.','A new page will open to connect your account.':'Een nieuwe pagina wordt geopend om verbinding te maken met uw account','We only extract images and never modify or delete them.':'We halen alleen uw afbeeldingen op en zullen deze nooit aanpassen of verwijderen','To disconnect from {providerName} click "Sign out" button in the menu.':'Om de verbinding met {providerName} te verbreken, klik je op "Afmelden" in het menu.','Sign in with Google':'Log in met Google','Go back':'Ga terug','This folder is empty.':'Deze map is leeg.',// Summary
Files:'Bestanden',Images:'Afbeeldingen',Uploaded:'Geüpload',Uploading:'Aan het uploaden',Completed:'Voltooid',Filter:'Zoeken','Cropped Images':'Bijgesneden afbeeldingen','Edited Images':'Bewerkte afbeeldingen','Selected Files':'Geselecteerde bestanden','Crop is required on images':'Uitsnede is vereist op afbeeldingen',// Transform
Crop:'Verkleinen',Circle:'Cirkel',Rotate:'Draaien',Mask:'Maskeren',Revert:'Ongedaan maken',Edit:'Bewerken',Reset:'Opnieuw zetten',Done:'Gedaan',Save:'Opslaan',Next:'Volgende','Edit Image':'Bewerk afbeelding','This image cannot be edited':'Deze afbeelding kan niet worden bewerkt',// Retry messaging
'Connection Lost':'Verbinding verbroken','Failed While Uploading':'Mislukt tijdens het uploaden','Retrying in':'Opnieuw proberen over','Try again':'Probeer het nog eens','Try now':'Probeer nu',// Local File Source
'Drag and Drop, Copy and Paste Files':'Sleep bestanden en zet ze neer, kopieer en plak bestanden','or Drag and Drop, Copy and Paste Files':'of slepen, kopiëren en plakken van bestanden','Select Files to Upload':'Selecteer bestanden om te uploaden','Select From':'Selecteren','Drop your files anywhere':'Zet je bestanden overal neer',// Input placeholders
'Enter a URL':'Voer een URL in','Search images':'Zoek beelden',// Webcam Source
'Webcam Disabled':'Webcam uitgeschakeld','Webcam Not Supported':'Webcam niet ondersteund','Please enable your webcam to take a photo.':'Schakel de webcam in om een foto te maken','Your current browser does not support webcam functionality.':'Deze browser heeft geen ondersteuning voor een webcam','We suggest using Chrome or Firefox.':'Wij raden aan om Chrome of Firefox te gebruiken.',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'Het bestandstype van {displayName} wordt niet geaccepteerd. Wel toegestane bestandstypen zijn: {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'Het {displayName} is te groot. De maximaal toegestane bestandsgrootte is: {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Het maximaal aantal, te uploaden, bestanden is {maxFiles} {filesText}','No search results found for "{search}"':'Geen zoekresultaten gevonden voor "{search}"','An error occurred. Please try again.':'Er is een fout opgetreden. Probeer het opnieuw.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Bestanden [{displayName}] zijn te groot. De geaccepteerde bestandsgrootte is {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Klik hier of druk op ESC om te sluiten'};var no$1={// Actions
Upload:'Last opp','Upload more':'Last opp mer','Deselect All':'Opphev alle','View/Edit Selected':'Vis/ rediger valgte','Sign Out':'Logg ut',// Source Labels
'My Device':'Min enhet','Web Search':'Nettsøk','Take Photo':'Ta bilde','Link (URL)':'Link (URL)','Record Video':'Ta opp video','Record Audio':'Ta opp lyd',// Custom Source
'Custom Source':'Egendefinert kilde',// Footer Text
Add:'Legg til','more file':'flere filer','more files':'flere filer',// Cloud
'Connect {providerName}':'Koble {providerName}','Select Files from {providerName}':'Velg filer fra {providerName}','You need to authenticate with {providerName}.':'Du må autentisere med {providerName}.','A new page will open to connect your account.':'En ny side vil åpnes for å koble til din konto','We only extract images and never modify or delete them.':'Vi trekker kun ut bilder og vil aldri endre eller slette dem','To disconnect from {providerName} click "Sign out" button in the menu.':'For å koble fra {providerName} klikker du på "Logg ut" -knappen i menyen.','Sign in with Google':'Logg på med Google','Go back':'Gå tilbake','This folder is empty.':'Denne mappen er tom.',// Summary
Files:'Filer',Images:'Bilder',Uploaded:'Lastet opp',Uploading:'Laster opp',Completed:'Fullført',Filter:'Filtrer','Cropped Images':'Filer og mapper','Edited Images':'Redigerte bilder','Selected Files':'Valgte filer','Crop is required on images':'Beskjæring er påkrevd for bilder',// Transform
Crop:'Beskjær',Circle:'Sirkel',Rotate:'Rotere',Mask:'Maske',Revert:'Tilbakestill',Edit:'Rediger',Reset:'Tilbakestill',Done:'Ferdig',Save:'Lagre',Next:'Neste','Edit Image':'Rediger bilde ','This image cannot be edited':'Dette bildet kan ikke redigeres',// Retry messaging
'Connection Lost':'Forbindelse mistet','Failed While Uploading':'Mislyktes under opplasting','Retrying in':'Prøver på nytt om','Try again':'Prøv igjen','Try now':'Prøv nå',// Local File Source
'Drag and Drop, Copy and Paste Files':'Dra og slipp, kopier og lim inn filer','or Drag and Drop, Copy and Paste Files':'eller dra og slipp, kopier og lim inn filer','Select Files to Upload':'Velg filer som skal lastes opp','Select From':'Velg fra','Drop your files anywhere':'Slett filene dine hvor som helst',// Input placeholders
'Enter a URL':'Skriv inn URL','Search images':'Søk bilder',// Webcam Source
'Webcam Disabled':'Webkamera deaktivert','Webcam Not Supported':'Webkamera ikke støttet','Please enable your webcam to take a photo.':'Vennligst aktiver ditt webkamera for å ta et bilde.','Your current browser does not support webcam functionality.':'Din nåværende nettleser støtter ikke webkamera funksjonalitet.','We suggest using Chrome or Firefox.':'Vi foreslår Chrome eller Firefox',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'Filen {displayName} er ikke en akseptert filtype. De godkjente filtyper er {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'Filen {displayName} er for stor. Akseptert filstørrelse er {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Vår filopplastingsgrense er {maxFiles} {filesText}','No search results found for "{search}"':'Ingen søkeresultater funnet for "{search}"','An error occurred. Please try again.':'En feil oppstod. Vær så snill, prøv på nytt.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Filene [{displayName}] er for store. Den aksepterte filstørrelsen er {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Klikk her eller trykk ESC for å lukke'};var pl={// Actions
Upload:'Prześlij pliki','Upload more':'Prześlij więcej','Deselect All':'Odznacz wszystko','View/Edit Selected':'Wyświetl/Edytuj zaznaczone','Sign Out':'Wyloguj się',// Source Labels
'My Device':'Moje urządzenie','Web Search':'Grafika z internetu','Take Photo':'Zrób zdjęcie','Link (URL)':'Adres URL','Record Video':'Nagrać wideo','Record Audio':'Nagrać dźwięk',// Custom Source
'Custom Source':'Niestandardowe źródło',// Footer Text
Add:'Dodaj','more file':'więcej plików','more files':'więcej plików',// Cloud
'Connect {providerName}':'Połącz z {providerName}','Select Files from {providerName}':'Wybierz pliki z {providerName}','You need to authenticate with {providerName}.':'Musisz zostać zautoryzowany przez {providerName}.','A new page will open to connect your account.':'Nowa strona zostanie otwarta w celu połączenia z Twoim kontem.','We only extract images and never modify or delete them.':'Mamy tylko wyodrębnić obrazy i nigdy zmodyfikować lub usunąć je','To disconnect from {providerName} click "Sign out" button in the menu.':'Aby rozłączyć się z {providerName} kliknij "Wyloguj się" w menu.','Sign in with Google':'Zaloguj się do Google','Go back':'Wróć','This folder is empty.':'Ten folder jest pusty.',// Summary
Files:'Pliki',Images:'Obrazy',Uploaded:'Przesłany',Uploading:'Przesyłanie danych',Completed:'Ukończono',Filter:'Szukaj','Cropped Images':'Przycięte obrazy','Edited Images':'Edytowane obrazy','Selected Files':'Wybrane pliki','Crop is required on images':'Przycinanie jest wymagane na obrazach',// Transform
Crop:'przytnij',Circle:'przytnij w kształcie koła',Rotate:'obróć',Mask:'dodaj warstwę',Revert:'Cofnij',Edit:'Edytuj',Reset:'przywróć oryginał',Done:'Gotowe',Save:'Zapisać',Next:'Kolejny','Edit Image':'Edytuj zdjęcie','This image cannot be edited':'Tego obrazu nie można edytować',// Retry messaging
'Connection Lost':'Utracono połączenie','Failed While Uploading':'Wystąpił błąd podczas przesyłania','Retrying in':'Ponawiam próbę','Try again':'Spróbuj ponownie','Try now':'Spróbuj teraz',// Local File Source
'Drag and Drop, Copy and Paste Files':'Przeciągnij i upuść pliki','or Drag and Drop, Copy and Paste Files':'lub przeciągnij i upuść, kopiować i wklejać pliki','Select Files to Upload':'Wybierz pliki do przesłania','Select From':'Wybierz z','Drop your files anywhere':'Upuść swoje pliki w dowolnym miejscu',// Input placeholders
'Enter a URL':'Wprowadź adres URL','Search images':'Szukaj obrazów',// Webcam Source
'Webcam Disabled':'Kamera jest wyłączona','Webcam Not Supported':'Kamera nie jest obsługiwana','Please enable your webcam to take a photo.':'Proszę włączyć kamerę internetową, aby zrobić zdjęcie','Your current browser does not support webcam functionality.':'Twoja przeglądarka obecnie nie obsługuje funkcji kamery internetowej.','We suggest using Chrome or Firefox.':'Zalecamy użycie Chrome lub Firefox',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'{displayName} Plik nie jest typem plików akceptowane. Typy plików akceptowane są {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{displayName} Plik jest zbyt duże. Rozmiar plików akceptowane jest {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Nasz limit uploadu pliku jest {maxFiles} {filesText}','No search results found for "{search}"':'Nie znaleziono wynikow dla "{search}"','An error occurred. Please try again.':'Wystąpił błąd. Spróbuj ponownie.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Pliki [{displayName}] są za duże. Akceptowany rozmiar pliku to {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Kliknij tutaj lub naciśnij ESC, aby zamknąć'};var pt={// Actions
Upload:'Carregar','Upload more':'Carregar mais','Deselect All':'Desmarcar todos','View/Edit Selected':'Exibir/Editar selecionada','Sign Out':'Desconectar',// Source Labels
'My Device':'Meu dispositivo','Web Search':'Buscar imagens na Web','Take Photo':'Tirar Foto','Link (URL)':'Link (URL)','Record Video':'Gravar vídeo','Record Audio':'Gravar audio',// Custom Source
'Custom Source':'Fonte Personalizada',// Footer Text
Add:'Adicionar','more file':'arquivo mais','more files':'mais arquivos',// Cloud
'Connect {providerName}':'Conecte o {providerName}','Select Files from {providerName}':'Selecione arquivos do {providerName}','You need to authenticate with {providerName}.':'Você precisa se autenticar com o {providerName}.','A new page will open to connect your account.':'Uma nova página será aberta para conectar a sua conta.','We only extract images and never modify or delete them.':'Nós apenas extraímos os arquivos selecionados e nunca os modificamos ou os removemos.','To disconnect from {providerName} click "Sign out" button in the menu.':'Para desconectar do {providerName}, clique no botão "Desconectar" no menu.','Sign in with Google':'Faça login no Google','Go back':'Volte','This folder is empty.':'Esta pasta está vazia.',// Summary
Files:'Ficheiros',Images:'Imagens',Uploaded:'Carregado',Uploading:'A Enviar',Completed:'Concluído',Filter:'Ordenar','Cropped Images':'Imagens Cortadas','Edited Images':'Imagens Editadas','Selected Files':'Arquivos selecionados','Crop is required on images':'A colheita é necessária em imagens',// Transform
Crop:'Cortar',Circle:'Círculo',Rotate:'Rodar',Mask:'Mascarar',Revert:'Desfazer',Edit:'Editar',Reset:'Recompor',Done:'Feito',Save:'Salve',Next:'Próximo','Edit Image':'Editar Imagem','This image cannot be edited':'Esta imagem não pode ser editada',// Retry messaging
'Failed While Uploading':'Falha ao enviar','Retrying in':'A Tentar Novamente em','Connection Lost':'Ligação perdida','Try again':'Tente novamente','Try now':'Tente agora',// Local File Source
'Drag and Drop, Copy and Paste Files':'Arraste e solte, copie e cole arquivos','or Drag and Drop, Copy and Paste Files':'ou arrastar, copiar e colar arquivos','Select Files to Upload':'Selecionar arquivos para upload','Select From':'Selecione de','Drop your files anywhere':'Solte seus arquivos em qualquer lugar',// Input placeholders
'Enter a URL':'Insira um URL','Search images':'Procurar fotos',// Webcam Source
'Webcam Disabled':'Webcam Desativada','Webcam Not Supported':'Webcam Não Suportada','Please enable your webcam to take a photo.':'Por favor, ative sua webcam para tirar uma foto','Your current browser does not support webcam functionality.':'Seu navegador atual não suporta conexão com a webcam.','We suggest using Chrome or Firefox.':'Nós sugerimos Chrome ou Firefox.',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':' Arquivo {displayName} não é um tipo de arquivo aceitos. Os tipos de arquivo aceitos são {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{displayName} Arquivo é muito grande. O tamanho de arquivo aceito é {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'É o nosso limite de upload de arquivo {maxFiles} {filesText}','No search results found for "{search}"':'Nenhum resultado de pesquisa encontrado para "{search}"','An error occurred. Please try again.':'Um erro ocorreu. Por favor, tente novamente.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Os arquivos [{displayName}] são muito grandes. O tamanho do arquivo aceito é {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Clique aqui ou pressione ESC para fechar'};var ru={// Actions
Upload:'загрузить','Upload more':'Загрузить больше','Deselect All':'отмена','View/Edit Selected':'просмотреть/редактировать','Sign Out':'выйти',// Source Labels
'My Device':'Моё устройство','Web Search':'Поиск изображений в сети','Take Photo':'Фотографировать','Link (URL)':'URL-адрес','Record Video':'Запись видео','Record Audio':'Запись аудио',// Custom Source
'Custom Source':'Пользовательский источник',// Footer Text
Add:'Добавить','more file':'более файла','more files':'больше файлов',// Cloud
'Connect {providerName}':'Подключите {providerName}','Select Files from {providerName}':'Выбрать файлы из {providerName}','You need to authenticate with {providerName}.':'Вам нужно пройти аутентификацию с помощью {providerName}.','A new page will open to connect your account.':'Новая страница открывается чтобы подключить ваш аккаунт','We only extract images and never modify or delete them.':'Мы просто извлекаем фото, а никогда не их изменяем или удалим','To disconnect from {providerName} click "Sign out" button in the menu.':'Чтобы отключиться от {providerName}, нажмите кнопку «Выйти» в меню.','Sign in with Google':'Войти через Google','Go back':'Вернитесь','This folder is empty.':'Эта папка пуста.',// Summary
Files:'файлы',Images:'фото',Uploaded:'загружены',Uploading:'Загружается',Completed:'Завершено',Filter:'сортировать по дате','Cropped Images':'Обрезанные изображения','Edited Images':'Отредактированные изображения','Selected Files':'Выбранные файлы','Crop is required on images':'Урожай требуется для изображений',// Transform
Crop:'отделка',Circle:'круг',Rotate:'вращаться',Mask:'скрывать',Revert:'Отменить',Edit:'Изменить',Reset:'сброс',Done:'законченный',Save:'Сохранить',Next:'Следующий','Edit Image':'редактировать изображение','This image cannot be edited':'Это изображение нельзя отредактировать',// Retry messaging
'Connection Lost':'Соединение потеряно','Failed While Uploading':'Ошибка при загрузке','Retrying in':'Повторная попытка через','Try again':'Попробуй еще раз','Try now':'Попробуй',// Local File Source
'Drag and Drop, Copy and Paste Files':'Перетаскивание, копирование и вставка файлов','or Drag and Drop, Copy and Paste Files':'или перетаскивать, копировать и вставлять файлы','Select Files to Upload':'Выберите файлы для загрузки','Select From':'выбрать в...','Drop your files anywhere':'Перетащите свои файлы куда угодно',// Input placeholders
'Enter a URL':'Введите URL-адрес','Search images':'поиск фото',// Webcam Source
'Webcam Disabled':'Веб-камера отключена','Webcam Not Supported':'Веб-камера не поддерживается','Please enable your webcam to take a photo.':'Пожалуйста, включите фотокамеру, чтобы сделать фотографию','Your current browser does not support webcam functionality.':'Текущий браузер не поддерживает функциональность веб-камеры','We suggest using Chrome or Firefox.':'Мы предлагаем использовать Firefox',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'{displayName} Файлов не является типом прикладываемых файлов. Типы прикладываемых файлов являются {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{displayName} Файл слишком большой. Признанных файл имеет размер {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Наш предел загрузки файлов {maxFiles} {filesText}','No search results found for "{search}"':'По запросу "{search}" ничего не найдено','An error occurred. Please try again.':'Произошла ошибка. Пожалуйста, попробуйте еще раз.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Файлы [{displayName}] слишком велики. Принятый размер файла: {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Нажмите здесь или нажмите ESC, чтобы закрыть'};var sv={// Actions
Upload:'Ladda upp','Upload more':'Ladda upp mer','Deselect All':'Avmarkera Alla','View/Edit Selected':'Visa/Editera Valda','Sign Out':'Logga ut',// Source Labels
'My Device':'Min enhet','Web Search':'Webbsökning','Take Photo':'Ta Ett Foto','Link (URL)':'URL-adress','Record Video':'Spela in video','Record Audio':'Spela in ljud',// Custom Source
'Custom Source':'Anpassad källa',// Footer Text
Add:'Lägg till','more file':'more fil','more files':'fler filer',// Cloud
'Connect {providerName}':'Anslut {providerName}','Select Files from {providerName}':'Välj filer från {providerName}','You need to authenticate with {providerName}.':'Du måste verifiera med google-enheten {providerName}.','A new page will open to connect your account.':'En ny sida öppnas för att ansluta ditt konto','We only extract images and never modify or delete them.':'Vi använder bara bildern och ändrar aldrig eller tar bort dem','To disconnect from {providerName} click "Sign out" button in the menu.':'För att koppla från {providerName}, klicka på "Logga ut" -knappen i menyn.','Sign in with Google':'Logga in med Google','Go back':'Gå tillbaka','This folder is empty.':'Denna mapp är tom.',// Summary
Files:'Filer',Images:'Bilder',Uploaded:'Uppladdade',Uploading:'Uppladdning',Completed:'Avslutad',Filter:'Filter','Cropped Images':'Beskurna Bilder','Edited Images':'Redigerad Bild','Selected Files':'Valda filer','Crop is required on images':'Beskär krävs på bilder',// Transform
Crop:'Beskära',Circle:'Cirkel',Rotate:'Rotera',Mask:'Maskera',Revert:'Invertera',Edit:'Editera',Reset:'Återställ',Done:'Gjort',Save:'Spara',Next:'Nästa','Edit Image':'Redigera Bild','This image cannot be edited':'Den här bilden kan inte redigeras',// Retry messaging
'Connection Lost':'Anslutning förlorad','Failed While Uploading':'Misslyckades Vid Uppladdning','Retrying in':'Försöker Igen','Try again':'Försök Igen','Try now':'Prova nu',// Local File Source
'Drag and Drop, Copy and Paste Files':'Dra och släpp, kopiera och klistra in filer','or Drag and Drop, Copy and Paste Files':'Eller dra och släpp, kopiera och klistra in filer','Select Files to Upload':'Välj dina filer för att ladda upp','Select From':'Välj från','Drop your files anywhere':'Släpp dina filer var som helst',// Input placeholders
'Enter a URL':'Ange en webbadress','Search images':'Sök bilder',// Webcam Source
'Webcam Disabled':'Webkameran Avaktiverad','Webcam Not Supported':'Webkameran är inte kompatibel','Please enable your webcam to take a photo.':'Vänligen aktivera din webkamera för att ta ett foto','Your current browser does not support webcam functionality.':'Din nuvarande webbläsare stöder inte webbkamera','We suggest using Chrome or Firefox.':'Vi föreslår att du använder Chrome eller Firefox.',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'Fil {displayName} är inte en accepterad filtyp. De accepterade filtyperna är {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'Fil {displayName} är för stor. Den accepterade filstorleken är mindre än {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Vår Filöverföringsgräns är {maxFiles} {filesText}','No search results found for "{search}"':'Inga sökresultat funna för "{search}"','An error occurred. Please try again.':'Ett fel uppstod. Var god försök igen.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Filer [{displayName}] är för stora. Den accepterade filstorleken är {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Klicka här eller tryck ESC för att stänga'};var tr={// Actions
Upload:'Resimleri Yükle','Deselect All':'Seçimi Kaldır','View/Edit Selected':'Görüntüle/Düzenle','Sign Out':'Çıkış Yap','Upload more':'Daha fazla ekle',// Source Labels
'My Device':'Bilgisayarım','Web Search':'Web Arama','Take Photo':'Fotoğraf Çek','Link (URL)':'Link','Record Video':'Video kaydetmek','Record Audio':'Ses kaydı',// Custom Source
'Custom Source':'Özel kaynak',// Footer Text
Add:'Ekle','more file':'Daha Fazla','more files':'Daha Fazla',// Cloud
'Connect {providerName}':'{providerName} bağlayın','Select Files from {providerName}':'{providerName} Dosyaları Seç','You need to authenticate with {providerName}.':'{providerName} sürücü ile kimlik doğrulaması yapmanız gerekir.','A new page will open to connect your account.':'Giriş yapman için yeni sayfa açılacak','We only extract images and never modify or delete them.':'Biz sadece resimlerinizi görüntüleriz, düzenleme ve silme işlemi yapmayız.','To disconnect from {providerName} click "Sign out" button in the menu.':'{providerName} ile bağlantısını kesmek için menüdeki "Çıkış Yap" butonuna tıklayın.','Sign in with Google':'Google ile giriş yap','Go back':'Geri dön','This folder is empty.':'Bu klasör boş.',// Summary
Files:'Dosyalar',Images:'Resimler',Uploaded:'Yüklendi',Uploading:'Yükleniyor',Completed:'Tamamlandı',Filter:'Filtre','Cropped Images':'Kesilen resimler','Edited Images':'Düzenlenen resimler','Selected Files':'Seçilen resimler','Crop is required on images':'Kesim işlemi resimlerde zorunludur',// Transform
Crop:'Kes',Circle:'Yuvarlak',Rotate:'Döndür',Mask:'Maskele',Revert:'Geri Al',Edit:'Düzenle',Reset:'Sıfırla',Done:'Tamam',Save:'Kaydet',Next:'Sonraki','Edit Image':'Resmi düzenle','This image cannot be edited':'Bu resim düzenlenemez',// Retry messaging
'Connection Lost':'İletişim koptu','Failed While Uploading':'Yüklenirken hata oluştu','Retrying in':'Tekrar denenecek: ','Try again':'Tekrar dene','Try now':'Şimdi tekrar dene',// Local File Source
'Drag and Drop, Copy and Paste Files':'Sürükle ve Bırak, Dosyaları Kopyala ve Yapıştır','or Drag and Drop, Copy and Paste Files':'veya sürükle bırak ya da buraya kopyala/yapıştır','Select Files to Upload':'Fotoğrafları yüklemek için seçin','Select From':'Seçiminizi yapın: ','Drop your files anywhere':'Resimlerinizi herhangi bir yere sürükleyin',// Input placeholders
'Enter a URL':'Link girin','Search images':'Resim ara',// Webcam Source
'Webcam Disabled':'Webcam Devre Dışı','Webcam Not Supported':'Webcam Desteklenmiyor','Please enable your webcam to take a photo.':'Lütfen web kameranızın fotoğraf çekmesini sağlayın.','Your current browser does not support webcam functionality.':'Mevcut tarayıcınız web kamerası işlevini desteklemiyor.','We suggest using Chrome or Firefox.':'Chrome veya Firefox kullanmanızı öneririz.',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'{displayName} isimli dosyanın tipi kabul edilmiyor. Kabul edilen dosya tipleri: {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'{displayName} isimli resim dosyasının boyutu çok büyük. Kabul edilen en yüksek dosya boyutu: {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Resim yükleme limiti {maxFiles} adet.','No search results found for "{search}"':'Arama sonucu "{search}"','An error occurred. Please try again.':'Bir hata oluştu. Lütfen tekrar deneyin.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'[{DisplayName}] dosyaları çok büyük. Kabul edilen dosya boyutu: {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Buraya tıklayın veya kapatmak için ESC basın.'};var vi={// Actions
Upload:'Tải lên','Upload more':'Tải lên nhiều hơn','Deselect All':'Bỏ chọn tất cả','View/Edit Selected':'Xem/Chỉnh sửa tập tin đã chọn','Sign Out':'Đăng xuất',// Source Labels
'My Device':'Thiết bị của tôi','Web Search':'Tìm kiếm trên web','Take Photo':'Chụp ảnh','Link (URL)':'Địa chỉ URL','Record Video':'Quay video','Record Audio':'Ghi âm',// Custom Source
'Custom Source':'Nguồn tùy chỉnh',// Footer Text
Add:'Thêm','more file':'Thêm tập tin','more files':'Thêm tập tin',// Cloud
'Connect {providerName}':'Kết nối {providerName}','Select Files from {providerName}':'Chọn tệp từ {providerName}','You need to authenticate with {providerName}.':'Bạn cần xác thực với {providerName}.','A new page will open to connect your account.':'Một trang kết nối với tài khoản của bạn sẽ được mở','We only extract images and never modify or delete them.':'Chúng tôi chỉ trích xuất hình ảnh và không bao giờ sửa đổi hoặc xóa chúng','To disconnect from {providerName} click "Sign out" button in the menu.':'Để ngắt kết nối khỏi {providerName}, nhấp vào nút "Đăng xuất" trong menu.','Sign in with Google':'Đăng nhập bằng Google','Go back':'Quay lại','This folder is empty.':'Thư mục này trống.',// Summary
Files:'Các tập tin',Images:'Hình ảnh',Uploaded:'Đã tải lên',Uploading:'Tải lên',Completed:'Đã hoàn thành',Filter:'Bộ lọc','Cropped Images':'Hình ảnh bị cắt','Edited Images':'Chỉnh sửa hình ảnh','Selected Files':'Tập tin đã được chọn','Crop is required on images':'Cây trồng được yêu cầu trên hình ảnh',// Transform
Crop:'Cắt',Circle:'Giới',Rotate:'Quay',Mask:'Mặt nạ',Revert:'Quay lại',Edit:'Chỉnh sửa',Reset:'Cài lại',Done:'Hoàn tất',Save:'Tiết kiệm',Next:'Kế tiếp','Edit Image':'Chỉnh sửa hình ảnh','This image cannot be edited':'Không thể chỉnh sửa hình ảnh này',// Retry messaging
'Connection Lost':'Kết nối bị mất','Failed While Uploading':'Không thể tải lên','Retrying in':'Đang thử lại','Try again':'Thử lại','Try now':'Thử ngay bây giờ',// Local File Source
'Drag and Drop, Copy and Paste Files':'Kéo và thả, sao chép và dán tập tin','or Drag and Drop, Copy and Paste Files':'hoặc Kéo và Thả, Sao chép và Dán Tập tin','Select Files to Upload':'Chọn tập tin để tải lên','Select From':'Chọn từ','Drop your files anywhere':'Thả tập tin của bạn bất cứ nơi nào',// Input placeholders
'Enter a URL':'Nhập URL','Search images':'Tìm kiểm hình ảnh',// Webcam Source
'Webcam Disabled':'Webcam bị vô hiệu hóa','Webcam Not Supported':'Webcam không được hỗ trợ','Please enable your webcam to take a photo.':'Hãy kích hoạt webcam của bạn để chụp ảnh.','Your current browser does not support webcam functionality.':'Trình duyệt hiện tại của bạn không hỗ trợ chức năng webcam.','We suggest using Chrome or Firefox.':'Chúng tôi khuyên bạn sử dụng Chrome hoặc Firefox.',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':'Tập tin {displayName} không phải là loại tập tin được chấp nhận. Các loại tập tin được chấp nhận là {types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':'Tập tin {displayName} quá lớn. Kích thước tập tin được chấp nhận là {roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'Giới hạn tập tin tải lên là {maxFiles} {filesText}','No search results found for "{search}"':'Không tìm thấy kết quả tìm kiếm cho "{search}"','An error occurred. Please try again.':'Xảy ra lỗi Vui lòng thử lại.','Files [{displayName}] are too big. The accepted file size is {maxSize}':'Tệp [{displayName}] quá lớn. Kích thước tệp được chấp nhận là {maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'Nhấn vào đây hoặc nhấn ESC để đóng'};var zh={// Actions
Upload:'上传','Upload more':'上传更多','Deselect All':'取消选择全部','View/Edit Selected':'查看/编辑所选项','Sign Out':'登出',// Source Labels
'My Device':'我的设备','Web Search':'为图片搜索网络','Take Photo':'拍照','Link (URL)':'网址','Record Video':'录视频','Record Audio':'录制音频',// Custom Source
'Custom Source':'自定义来源',// Footer Text
Add:'再添加','more file':'更多文件','more files':'更多文件',// Cloud
'Connect {providerName}':'连接{providerName}','Select Files from {providerName}':'从{providerName}中选择文件','You need to authenticate with {providerName}.':'您需要使用{providerName}驱动器进行身份验证。','A new page will open to connect your account.':'一个新页面会打开以关联您的帐户','We only extract images and never modify or delete them.':'我们只提取图像，从不修改或删除它们','To disconnect from {providerName} click "Sign out" button in the menu.':'要断开与{providerName}的连接，请单击菜单中的“登出”按钮。','Sign in with Google':'使用Google登录','Go back':'回去','This folder is empty.':'这个文件夹是空的。',// Summary
Files:'文件',Images:'图片',Uploaded:'已上传',Uploading:'正在上传',Completed:'已完成',Filter:'过滤','Cropped Images':'裁剪图片','Edited Images':'编辑的图像','Selected Files':'所选文件','Crop is required on images':'图像上需要裁剪',// Transform
Crop:'剪',Circle:'圆',Rotate:'回转',Mask:'重新盖上',Revert:'还原',Edit:'编辑',Reset:'重启',Done:'毕',Save:'保存',Next:'下一个','Edit Image':'编辑图像','This image cannot be edited':'此图像无法编辑',// Retry messaging
'Connection Lost':'连接中断','Failed While Uploading':'上传失败','Retrying in':'重试倒计时','Try again':'再试一次','Try now':'现在试试',// Local File Source
'Drag and Drop, Copy and Paste Files':'拖放，复制和粘贴文件','or Drag and Drop, Copy and Paste Files':'或拖放，复制和粘贴文件','Select Files to Upload':'选择要上传的文件','Select From':'选择自','Drop your files anywhere':'将文件放在任何地方',// Input placeholders
'Enter a URL':'输入一个网址','Search images':'搜索图像',// Webcam Source
'Webcam Disabled':'摄像头关闭','Webcam Not Supported':'不支持摄像头','Please enable your webcam to take a photo.':'请打开摄像头','Your current browser does not support webcam functionality.':'您的浏览器不支持网络摄像头功能。','We suggest using Chrome or Firefox.':'我们建议使用火狐。',// Error Notifications
'File {displayName} is not an accepted file type. The accepted file types are {types}':' 文件{displayName}不是可接受的文件类型。 可接受的文件类型为{types}','File {displayName} is too big. The accepted file size is less than {roundFileSize}':' 文件{displayName}太大。 可接受的文件大小为{roundFileSize}','Our file upload limit is {maxFiles} {filesText}':'我们的文件上传限制为 {maxFiles} {filesText}','No search results found for "{search}"':'未找到{search}的搜索结果','An error occurred. Please try again.':'发生错误。 请再试一次。','Files [{displayName}] are too big. The accepted file size is {maxSize}':'文件[{displayName}]太大。 可接受的文件大小为{maxSize}',// Other UI labels and titles
'Click here or hit ESC to close picker':'单击此处或按ESC键关闭'};var languages={ar:ar,ca:ca,da:da,de:de,en:en,es:es,fr:fr,he:he,it:it,ja:ja,ko:ko,nl:nl,no:no$1,pl:pl,pt:pt,ru:ru,sv:sv,tr:tr,vi:vi,zh:zh};var errors=function errors(){var lang=arguments.length>0&&arguments[0]!==undefined?arguments[0]:'en';var customText=arguments.length>1&&arguments[1]!==undefined?arguments[1]:{};var phrases=_objectSpread({},languages[lang],{},customText);var getPhrase=function getPhrase(p){return phrases[p]||p;};return{ERROR_FILE_NOT_ACCEPTABLE:getPhrase('File {displayName} is not an accepted file type. The accepted file types are {types}'),ERROR_FILE_TOO_BIG:getPhrase('File {displayName} is too big. The accepted file size is less than {roundFileSize}'),ERROR_FILES_TOO_BIG:getPhrase('Files [{displayName}] are too big. The accepted file size is {maxSize}'),ERROR_MAX_FILES_REACHED:getPhrase('Our file upload limit is {maxFiles} {filesText}')};};var pica=createCommonjsModule(function(module,exports){/* pica 4.2.0 nodeca/pica */(function(f){{module.exports=f();}})(function(){return function(){function r(e,n,t){function o(i,f){if(!n[i]){if(!e[i]){var c="function"==typeof commonjsRequire&&commonjsRequire;if(!f&&c)return c(i,!0);if(u)return u(i,!0);var a=new Error("Cannot find module '"+i+"'");throw a.code="MODULE_NOT_FOUND",a;}var p=n[i]={exports:{}};e[i][0].call(p.exports,function(r){var n=e[i][1][r];return o(n||r);},p,p.exports,r,e,n,t);}return n[i].exports;}for(var u="function"==typeof commonjsRequire&&commonjsRequire,i=0;i<t.length;i++){o(t[i]);}return o;}return r;}()({1:[function(require,module,exports){var inherits=require('inherits');var Multimath=require('multimath');var mm_unsharp_mask=require('multimath/lib/unsharp_mask');var mm_resize=require('./mm_resize');function MathLib(requested_features){var __requested_features=requested_features||[];var features={js:__requested_features.indexOf('js')>=0,wasm:__requested_features.indexOf('wasm')>=0};Multimath.call(this,features);this.features={js:features.js,wasm:features.wasm&&this.has_wasm};this.use(mm_unsharp_mask);this.use(mm_resize);}inherits(MathLib,Multimath);MathLib.prototype.resizeAndUnsharp=function resizeAndUnsharp(options,cache){var result=this.resize(options,cache);if(options.unsharpAmount){this.unsharp_mask(result,options.toWidth,options.toHeight,options.unsharpAmount,options.unsharpRadius,options.unsharpThreshold);}return result;};module.exports=MathLib;},{"./mm_resize":4,"inherits":15,"multimath":16,"multimath/lib/unsharp_mask":19}],2:[function(require,module,exports){// Precision of fixed FP values
//var FIXED_FRAC_BITS = 14;
function clampTo8(i){return i<0?0:i>255?255:i;}// Convolve image in horizontal directions and transpose output. In theory,
// transpose allow:
//
// - use the same convolver for both passes (this fails due different
//   types of input array and temporary buffer)
// - making vertical pass by horisonltal lines inprove CPU cache use.
//
// But in real life this doesn't work :)
//
function convolveHorizontally(src,dest,srcW,srcH,destW,filters){var r,g,b,a;var filterPtr,filterShift,filterSize;var srcPtr,srcY,destX,filterVal;var srcOffset=0,destOffset=0;// For each row
for(srcY=0;srcY<srcH;srcY++){filterPtr=0;// Apply precomputed filters to each destination row point
for(destX=0;destX<destW;destX++){// Get the filter that determines the current output pixel.
filterShift=filters[filterPtr++];filterSize=filters[filterPtr++];srcPtr=srcOffset+filterShift*4|0;r=g=b=a=0;// Apply the filter to the row to get the destination pixel r, g, b, a
for(;filterSize>0;filterSize--){filterVal=filters[filterPtr++];// Use reverse order to workaround deopts in old v8 (node v.10)
// Big thanks to @mraleph (Vyacheslav Egorov) for the tip.
a=a+filterVal*src[srcPtr+3]|0;b=b+filterVal*src[srcPtr+2]|0;g=g+filterVal*src[srcPtr+1]|0;r=r+filterVal*src[srcPtr]|0;srcPtr=srcPtr+4|0;}// Bring this value back in range. All of the filter scaling factors
// are in fixed point with FIXED_FRAC_BITS bits of fractional part.
//
// (!) Add 1/2 of value before clamping to get proper rounding. In other
// case brightness loss will be noticeable if you resize image with white
// border and place it on white background.
//
dest[destOffset+3]=clampTo8(a+(1<<13)>>14/*FIXED_FRAC_BITS*/);dest[destOffset+2]=clampTo8(b+(1<<13)>>14/*FIXED_FRAC_BITS*/);dest[destOffset+1]=clampTo8(g+(1<<13)>>14/*FIXED_FRAC_BITS*/);dest[destOffset]=clampTo8(r+(1<<13)>>14/*FIXED_FRAC_BITS*/);destOffset=destOffset+srcH*4|0;}destOffset=(srcY+1)*4|0;srcOffset=(srcY+1)*srcW*4|0;}}// Technically, convolvers are the same. But input array and temporary
// buffer can be of different type (especially, in old browsers). So,
// keep code in separate functions to avoid deoptimizations & speed loss.
function convolveVertically(src,dest,srcW,srcH,destW,filters){var r,g,b,a;var filterPtr,filterShift,filterSize;var srcPtr,srcY,destX,filterVal;var srcOffset=0,destOffset=0;// For each row
for(srcY=0;srcY<srcH;srcY++){filterPtr=0;// Apply precomputed filters to each destination row point
for(destX=0;destX<destW;destX++){// Get the filter that determines the current output pixel.
filterShift=filters[filterPtr++];filterSize=filters[filterPtr++];srcPtr=srcOffset+filterShift*4|0;r=g=b=a=0;// Apply the filter to the row to get the destination pixel r, g, b, a
for(;filterSize>0;filterSize--){filterVal=filters[filterPtr++];// Use reverse order to workaround deopts in old v8 (node v.10)
// Big thanks to @mraleph (Vyacheslav Egorov) for the tip.
a=a+filterVal*src[srcPtr+3]|0;b=b+filterVal*src[srcPtr+2]|0;g=g+filterVal*src[srcPtr+1]|0;r=r+filterVal*src[srcPtr]|0;srcPtr=srcPtr+4|0;}// Bring this value back in range. All of the filter scaling factors
// are in fixed point with FIXED_FRAC_BITS bits of fractional part.
//
// (!) Add 1/2 of value before clamping to get proper rounding. In other
// case brightness loss will be noticeable if you resize image with white
// border and place it on white background.
//
dest[destOffset+3]=clampTo8(a+(1<<13)>>14/*FIXED_FRAC_BITS*/);dest[destOffset+2]=clampTo8(b+(1<<13)>>14/*FIXED_FRAC_BITS*/);dest[destOffset+1]=clampTo8(g+(1<<13)>>14/*FIXED_FRAC_BITS*/);dest[destOffset]=clampTo8(r+(1<<13)>>14/*FIXED_FRAC_BITS*/);destOffset=destOffset+srcH*4|0;}destOffset=(srcY+1)*4|0;srcOffset=(srcY+1)*srcW*4|0;}}module.exports={convolveHorizontally:convolveHorizontally,convolveVertically:convolveVertically};},{}],3:[function(require,module,exports){/* eslint-disable max-len */module.exports='AGFzbQEAAAABFAJgBn9/f39/fwBgB39/f39/f38AAg8BA2VudgZtZW1vcnkCAAEDAwIAAQQEAXAAAAcZAghjb252b2x2ZQAACmNvbnZvbHZlSFYAAQkBAArmAwLBAwEQfwJAIANFDQAgBEUNACAFQQRqIRVBACEMQQAhDQNAIA0hDkEAIRFBACEHA0AgB0ECaiESAn8gBSAHQQF0IgdqIgZBAmouAQAiEwRAQQAhCEEAIBNrIRQgFSAHaiEPIAAgDCAGLgEAakECdGohEEEAIQlBACEKQQAhCwNAIBAoAgAiB0EYdiAPLgEAIgZsIAtqIQsgB0H/AXEgBmwgCGohCCAHQRB2Qf8BcSAGbCAKaiEKIAdBCHZB/wFxIAZsIAlqIQkgD0ECaiEPIBBBBGohECAUQQFqIhQNAAsgEiATagwBC0EAIQtBACEKQQAhCUEAIQggEgshByABIA5BAnRqIApBgMAAakEOdSIGQf8BIAZB/wFIG0EQdEGAgPwHcUEAIAZBAEobIAtBgMAAakEOdSIGQf8BIAZB/wFIG0EYdEEAIAZBAEobciAJQYDAAGpBDnUiBkH/ASAGQf8BSBtBCHRBgP4DcUEAIAZBAEobciAIQYDAAGpBDnUiBkH/ASAGQf8BSBtB/wFxQQAgBkEAShtyNgIAIA4gA2ohDiARQQFqIhEgBEcNAAsgDCACaiEMIA1BAWoiDSADRw0ACwsLIQACQEEAIAIgAyAEIAUgABAAIAJBACAEIAUgBiABEAALCw==';},{}],4:[function(require,module,exports){module.exports={name:'resize',fn:require('./resize'),wasm_fn:require('./resize_wasm'),wasm_src:require('./convolve_wasm_base64')};},{"./convolve_wasm_base64":3,"./resize":5,"./resize_wasm":8}],5:[function(require,module,exports){var createFilters=require('./resize_filter_gen');var convolveHorizontally=require('./convolve').convolveHorizontally;var convolveVertically=require('./convolve').convolveVertically;function resetAlpha(dst,width,height){var ptr=3,len=width*height*4|0;while(ptr<len){dst[ptr]=0xFF;ptr=ptr+4|0;}}module.exports=function resize(options){var src=options.src;var srcW=options.width;var srcH=options.height;var destW=options.toWidth;var destH=options.toHeight;var scaleX=options.scaleX||options.toWidth/options.width;var scaleY=options.scaleY||options.toHeight/options.height;var offsetX=options.offsetX||0;var offsetY=options.offsetY||0;var dest=options.dest||new Uint8Array(destW*destH*4);var quality=typeof options.quality==='undefined'?3:options.quality;var alpha=options.alpha||false;var filtersX=createFilters(quality,srcW,destW,scaleX,offsetX),filtersY=createFilters(quality,srcH,destH,scaleY,offsetY);var tmp=new Uint8Array(destW*srcH*4);// To use single function we need src & tmp of the same type.
// But src can be CanvasPixelArray, and tmp - Uint8Array. So, keep
// vertical and horizontal passes separately to avoid deoptimization.
convolveHorizontally(src,tmp,srcW,srcH,destW,filtersX);convolveVertically(tmp,dest,srcH,destW,destH,filtersY);// That's faster than doing checks in convolver.
// !!! Note, canvas data is not premultipled. We don't need other
// alpha corrections.
if(!alpha)resetAlpha(dest,destW,destH);return dest;};},{"./convolve":2,"./resize_filter_gen":6}],6:[function(require,module,exports){var FILTER_INFO=require('./resize_filter_info');// Precision of fixed FP values
var FIXED_FRAC_BITS=14;function toFixedPoint(num){return Math.round(num*((1<<FIXED_FRAC_BITS)-1));}module.exports=function resizeFilterGen(quality,srcSize,destSize,scale,offset){var filterFunction=FILTER_INFO[quality].filter;var scaleInverted=1.0/scale;var scaleClamped=Math.min(1.0,scale);// For upscale
// Filter window (averaging interval), scaled to src image
var srcWindow=FILTER_INFO[quality].win/scaleClamped;var destPixel,srcPixel,srcFirst,srcLast,filterElementSize,floatFilter,fxpFilter,total,pxl,idx,floatVal,filterTotal,filterVal;var leftNotEmpty,rightNotEmpty,filterShift,filterSize;var maxFilterElementSize=Math.floor((srcWindow+1)*2);var packedFilter=new Int16Array((maxFilterElementSize+2)*destSize);var packedFilterPtr=0;var slowCopy=!packedFilter.subarray||!packedFilter.set;// For each destination pixel calculate source range and built filter values
for(destPixel=0;destPixel<destSize;destPixel++){// Scaling should be done relative to central pixel point
srcPixel=(destPixel+0.5)*scaleInverted+offset;srcFirst=Math.max(0,Math.floor(srcPixel-srcWindow));srcLast=Math.min(srcSize-1,Math.ceil(srcPixel+srcWindow));filterElementSize=srcLast-srcFirst+1;floatFilter=new Float32Array(filterElementSize);fxpFilter=new Int16Array(filterElementSize);total=0.0;// Fill filter values for calculated range
for(pxl=srcFirst,idx=0;pxl<=srcLast;pxl++,idx++){floatVal=filterFunction((pxl+0.5-srcPixel)*scaleClamped);total+=floatVal;floatFilter[idx]=floatVal;}// Normalize filter, convert to fixed point and accumulate conversion error
filterTotal=0;for(idx=0;idx<floatFilter.length;idx++){filterVal=floatFilter[idx]/total;filterTotal+=filterVal;fxpFilter[idx]=toFixedPoint(filterVal);}// Compensate normalization error, to minimize brightness drift
fxpFilter[destSize>>1]+=toFixedPoint(1.0-filterTotal);//
// Now pack filter to useable form
//
// 1. Trim heading and tailing zero values, and compensate shitf/length
// 2. Put all to single array in this format:
//
//    [ pos shift, data length, value1, value2, value3, ... ]
//
leftNotEmpty=0;while(leftNotEmpty<fxpFilter.length&&fxpFilter[leftNotEmpty]===0){leftNotEmpty++;}if(leftNotEmpty<fxpFilter.length){rightNotEmpty=fxpFilter.length-1;while(rightNotEmpty>0&&fxpFilter[rightNotEmpty]===0){rightNotEmpty--;}filterShift=srcFirst+leftNotEmpty;filterSize=rightNotEmpty-leftNotEmpty+1;packedFilter[packedFilterPtr++]=filterShift;// shift
packedFilter[packedFilterPtr++]=filterSize;// size
if(!slowCopy){packedFilter.set(fxpFilter.subarray(leftNotEmpty,rightNotEmpty+1),packedFilterPtr);packedFilterPtr+=filterSize;}else{// fallback for old IE < 11, without subarray/set methods
for(idx=leftNotEmpty;idx<=rightNotEmpty;idx++){packedFilter[packedFilterPtr++]=fxpFilter[idx];}}}else{// zero data, write header only
packedFilter[packedFilterPtr++]=0;// shift
packedFilter[packedFilterPtr++]=0;// size
}}return packedFilter;};},{"./resize_filter_info":7}],7:[function(require,module,exports){module.exports=[{// Nearest neibor (Box)
win:0.5,filter:function filter(x){return x>=-0.5&&x<0.5?1.0:0.0;}},{// Hamming
win:1.0,filter:function filter(x){if(x<=-1.0||x>=1.0){return 0.0;}if(x>-1.19209290E-07&&x<1.19209290E-07){return 1.0;}var xpi=x*Math.PI;return Math.sin(xpi)/xpi*(0.54+0.46*Math.cos(xpi/1.0));}},{// Lanczos, win = 2
win:2.0,filter:function filter(x){if(x<=-2.0||x>=2.0){return 0.0;}if(x>-1.19209290E-07&&x<1.19209290E-07){return 1.0;}var xpi=x*Math.PI;return Math.sin(xpi)/xpi*Math.sin(xpi/2.0)/(xpi/2.0);}},{// Lanczos, win = 3
win:3.0,filter:function filter(x){if(x<=-3.0||x>=3.0){return 0.0;}if(x>-1.19209290E-07&&x<1.19209290E-07){return 1.0;}var xpi=x*Math.PI;return Math.sin(xpi)/xpi*Math.sin(xpi/3.0)/(xpi/3.0);}}];},{}],8:[function(require,module,exports){var createFilters=require('./resize_filter_gen');function resetAlpha(dst,width,height){var ptr=3,len=width*height*4|0;while(ptr<len){dst[ptr]=0xFF;ptr=ptr+4|0;}}function asUint8Array(src){return new Uint8Array(src.buffer,0,src.byteLength);}var IS_LE=true;// should not crash everything on module load in old browsers
try{IS_LE=new Uint32Array(new Uint8Array([1,0,0,0]).buffer)[0]===1;}catch(__){}function copyInt16asLE(src,target,target_offset){if(IS_LE){target.set(asUint8Array(src),target_offset);return;}for(var ptr=target_offset,i=0;i<src.length;i++){var data=src[i];target[ptr++]=data&0xFF;target[ptr++]=data>>8&0xFF;}}module.exports=function resize_wasm(options){var src=options.src;var srcW=options.width;var srcH=options.height;var destW=options.toWidth;var destH=options.toHeight;var scaleX=options.scaleX||options.toWidth/options.width;var scaleY=options.scaleY||options.toHeight/options.height;var offsetX=options.offsetX||0.0;var offsetY=options.offsetY||0.0;var dest=options.dest||new Uint8Array(destW*destH*4);var quality=typeof options.quality==='undefined'?3:options.quality;var alpha=options.alpha||false;var filtersX=createFilters(quality,srcW,destW,scaleX,offsetX),filtersY=createFilters(quality,srcH,destH,scaleY,offsetY);// destination is 0 too.
var src_offset=0;// buffer between convolve passes
var tmp_offset=this.__align(src_offset+Math.max(src.byteLength,dest.byteLength));var filtersX_offset=this.__align(tmp_offset+srcH*destW*4);var filtersY_offset=this.__align(filtersX_offset+filtersX.byteLength);var alloc_bytes=filtersY_offset+filtersY.byteLength;var instance=this.__instance('resize',alloc_bytes);//
// Fill memory block with data to process
//
var mem=new Uint8Array(this.__memory.buffer);var mem32=new Uint32Array(this.__memory.buffer);// 32-bit copy is much faster in chrome
var src32=new Uint32Array(src.buffer);mem32.set(src32);// We should guarantee LE bytes order. Filters are not big, so
// speed difference is not significant vs direct .set()
copyInt16asLE(filtersX,mem,filtersX_offset);copyInt16asLE(filtersY,mem,filtersY_offset);//
// Now call webassembly method
// emsdk does method names with '_'
var fn=instance.exports.convolveHV||instance.exports._convolveHV;fn(filtersX_offset,filtersY_offset,tmp_offset,srcW,srcH,destW,destH);//
// Copy data back to typed array
//
// 32-bit copy is much faster in chrome
var dest32=new Uint32Array(dest.buffer);dest32.set(new Uint32Array(this.__memory.buffer,0,destH*destW));// That's faster than doing checks in convolver.
// !!! Note, canvas data is not premultipled. We don't need other
// alpha corrections.
if(!alpha)resetAlpha(dest,destW,destH);return dest;};},{"./resize_filter_gen":6}],9:[function(require,module,exports){var GC_INTERVAL=100;function Pool(create,idle){this.create=create;this.available=[];this.acquired={};this.lastId=1;this.timeoutId=0;this.idle=idle||2000;}Pool.prototype.acquire=function(){var _this=this;var resource=void 0;if(this.available.length!==0){resource=this.available.pop();}else{resource=this.create();resource.id=this.lastId++;resource.release=function(){return _this.release(resource);};}this.acquired[resource.id]=resource;return resource;};Pool.prototype.release=function(resource){var _this2=this;delete this.acquired[resource.id];resource.lastUsed=Date.now();this.available.push(resource);if(this.timeoutId===0){this.timeoutId=setTimeout(function(){return _this2.gc();},GC_INTERVAL);}};Pool.prototype.gc=function(){var _this3=this;var now=Date.now();this.available=this.available.filter(function(resource){if(now-resource.lastUsed>_this3.idle){resource.destroy();return false;}return true;});if(this.available.length!==0){this.timeoutId=setTimeout(function(){return _this3.gc();},GC_INTERVAL);}else{this.timeoutId=0;}};module.exports=Pool;},{}],10:[function(require,module,exports){// min size = 0 results in infinite loop,
// min size = 1 can consume large amount of memory
var MIN_INNER_TILE_SIZE=2;module.exports=function createStages(fromWidth,fromHeight,toWidth,toHeight,srcTileSize,destTileBorder){var scaleX=toWidth/fromWidth;var scaleY=toHeight/fromHeight;// derived from createRegions equation:
// innerTileWidth = pixelFloor(srcTileSize * scaleX) - 2 * destTileBorder;
var minScale=(2*destTileBorder+MIN_INNER_TILE_SIZE+1)/srcTileSize;// refuse to scale image multiple times by less than twice each time,
// it could only happen because of invalid options
if(minScale>0.5)return[[toWidth,toHeight]];var stageCount=Math.ceil(Math.log(Math.min(scaleX,scaleY))/Math.log(minScale));// no additional resizes are necessary,
// stageCount can be zero or be negative when enlarging the image
if(stageCount<=1)return[[toWidth,toHeight]];var result=[];for(var i=0;i<stageCount;i++){var width=Math.round(Math.pow(Math.pow(fromWidth,stageCount-i-1)*Math.pow(toWidth,i+1),1/stageCount));var height=Math.round(Math.pow(Math.pow(fromHeight,stageCount-i-1)*Math.pow(toHeight,i+1),1/stageCount));result.push([width,height]);}return result;};},{}],11:[function(require,module,exports){/*
   * pixelFloor and pixelCeil are modified versions of Math.floor and Math.ceil
   * functions which take into account floating point arithmetic errors.
   * Those errors can cause undesired increments/decrements of sizes and offsets:
   * Math.ceil(36 / (36 / 500)) = 501
   * pixelCeil(36 / (36 / 500)) = 500
   */var PIXEL_EPSILON=1e-5;function pixelFloor(x){var nearest=Math.round(x);if(Math.abs(x-nearest)<PIXEL_EPSILON){return nearest;}return Math.floor(x);}function pixelCeil(x){var nearest=Math.round(x);if(Math.abs(x-nearest)<PIXEL_EPSILON){return nearest;}return Math.ceil(x);}module.exports=function createRegions(options){var scaleX=options.toWidth/options.width;var scaleY=options.toHeight/options.height;var innerTileWidth=pixelFloor(options.srcTileSize*scaleX)-2*options.destTileBorder;var innerTileHeight=pixelFloor(options.srcTileSize*scaleY)-2*options.destTileBorder;// prevent infinite loop, this should never happen
if(innerTileWidth<1||innerTileHeight<1){throw new Error('Internal error in pica: target tile width/height is too small.');}var x,y;var innerX,innerY,toTileWidth,toTileHeight;var tiles=[];var tile;// we go top-to-down instead of left-to-right to make image displayed from top to
// doesn in the browser
for(innerY=0;innerY<options.toHeight;innerY+=innerTileHeight){for(innerX=0;innerX<options.toWidth;innerX+=innerTileWidth){x=innerX-options.destTileBorder;if(x<0){x=0;}toTileWidth=innerX+innerTileWidth+options.destTileBorder-x;if(x+toTileWidth>=options.toWidth){toTileWidth=options.toWidth-x;}y=innerY-options.destTileBorder;if(y<0){y=0;}toTileHeight=innerY+innerTileHeight+options.destTileBorder-y;if(y+toTileHeight>=options.toHeight){toTileHeight=options.toHeight-y;}tile={toX:x,toY:y,toWidth:toTileWidth,toHeight:toTileHeight,toInnerX:innerX,toInnerY:innerY,toInnerWidth:innerTileWidth,toInnerHeight:innerTileHeight,offsetX:x/scaleX-pixelFloor(x/scaleX),offsetY:y/scaleY-pixelFloor(y/scaleY),scaleX:scaleX,scaleY:scaleY,x:pixelFloor(x/scaleX),y:pixelFloor(y/scaleY),width:pixelCeil(toTileWidth/scaleX),height:pixelCeil(toTileHeight/scaleY)};tiles.push(tile);}}return tiles;};},{}],12:[function(require,module,exports){function objClass(obj){return Object.prototype.toString.call(obj);}module.exports.isCanvas=function isCanvas(element){//return (element.nodeName && element.nodeName.toLowerCase() === 'canvas') ||
var cname=objClass(element);return cname==='[object HTMLCanvasElement]'/* browser */||cname==='[object Canvas]'/* node-canvas */;};module.exports.isImage=function isImage(element){//return element.nodeName && element.nodeName.toLowerCase() === 'img';
return objClass(element)==='[object HTMLImageElement]';};module.exports.limiter=function limiter(concurrency){var active=0,queue=[];function roll(){if(active<concurrency&&queue.length){active++;queue.shift()();}}return function limit(fn){return new Promise(function(resolve,reject){queue.push(function(){fn().then(function(result){resolve(result);active--;roll();},function(err){reject(err);active--;roll();});});roll();});};};module.exports.cib_quality_name=function cib_quality_name(num){switch(num){case 0:return'pixelated';case 1:return'low';case 2:return'medium';}return'high';};module.exports.cib_support=function cib_support(){return Promise.resolve().then(function(){if(typeof createImageBitmap==='undefined'||typeof document==='undefined'){return false;}var c=document.createElement('canvas');c.width=100;c.height=100;return createImageBitmap(c,0,0,100,100,{resizeWidth:10,resizeHeight:10,resizeQuality:'high'}).then(function(bitmap){var status=bitmap.width===10;// Branch below is filtered on upper level. We do not call resize
// detection for basic ImageBitmap.
//
// https://developer.mozilla.org/en-US/docs/Web/API/ImageBitmap
// old Crome 51 has ImageBitmap without .close(). Then this code
// will throw and return 'false' as expected.
//
bitmap.close();c=null;return status;});})["catch"](function(){return false;});};},{}],13:[function(require,module,exports){module.exports=function(){var MathLib=require('./mathlib');var mathLib=void 0;/* eslint-disable no-undef */onmessage=function onmessage(ev){var opts=ev.data.opts;if(!mathLib)mathLib=new MathLib(ev.data.features);// Use multimath's sync auto-init. Avoid Promise use in old browsers,
// because polyfills are not propagated to webworker.
var result=mathLib.resizeAndUnsharp(opts);postMessage({result:result},[result.buffer]);};};},{"./mathlib":1}],14:[function(require,module,exports){// Calculate Gaussian blur of an image using IIR filter
// The method is taken from Intel's white paper and code example attached to it:
// https://software.intel.com/en-us/articles/iir-gaussian-blur-filter
// -implementation-using-intel-advanced-vector-extensions
var a0,a1,a2,a3,b1,b2,left_corner,right_corner;function gaussCoef(sigma){if(sigma<0.5){sigma=0.5;}var a=Math.exp(0.726*0.726)/sigma,g1=Math.exp(-a),g2=Math.exp(-2*a),k=(1-g1)*(1-g1)/(1+2*a*g1-g2);a0=k;a1=k*(a-1)*g1;a2=k*(a+1)*g1;a3=-k*g2;b1=2*g1;b2=-g2;left_corner=(a0+a1)/(1-b1-b2);right_corner=(a2+a3)/(1-b1-b2);// Attempt to force type to FP32.
return new Float32Array([a0,a1,a2,a3,b1,b2,left_corner,right_corner]);}function convolveMono16(src,out,line,coeff,width,height){// takes src image and writes the blurred and transposed result into out
var prev_src,curr_src,curr_out,prev_out,prev_prev_out;var src_index,out_index,line_index;var i,j;var coeff_a0,coeff_a1,coeff_b1,coeff_b2;for(i=0;i<height;i++){src_index=i*width;out_index=i;line_index=0;// left to right
prev_src=src[src_index];prev_prev_out=prev_src*coeff[6];prev_out=prev_prev_out;coeff_a0=coeff[0];coeff_a1=coeff[1];coeff_b1=coeff[4];coeff_b2=coeff[5];for(j=0;j<width;j++){curr_src=src[src_index];curr_out=curr_src*coeff_a0+prev_src*coeff_a1+prev_out*coeff_b1+prev_prev_out*coeff_b2;prev_prev_out=prev_out;prev_out=curr_out;prev_src=curr_src;line[line_index]=prev_out;line_index++;src_index++;}src_index--;line_index--;out_index+=height*(width-1);// right to left
prev_src=src[src_index];prev_prev_out=prev_src*coeff[7];prev_out=prev_prev_out;curr_src=prev_src;coeff_a0=coeff[2];coeff_a1=coeff[3];for(j=width-1;j>=0;j--){curr_out=curr_src*coeff_a0+prev_src*coeff_a1+prev_out*coeff_b1+prev_prev_out*coeff_b2;prev_prev_out=prev_out;prev_out=curr_out;prev_src=curr_src;curr_src=src[src_index];out[out_index]=line[line_index]+prev_out;src_index--;line_index--;out_index-=height;}}}function blurMono16(src,width,height,radius){// Quick exit on zero radius
if(!radius){return;}var out=new Uint16Array(src.length),tmp_line=new Float32Array(Math.max(width,height));var coeff=gaussCoef(radius);convolveMono16(src,out,tmp_line,coeff,width,height);convolveMono16(out,src,tmp_line,coeff,height,width);}module.exports=blurMono16;},{}],15:[function(require,module,exports){if(typeof Object.create==='function'){// implementation from standard node.js 'util' module
module.exports=function inherits(ctor,superCtor){ctor.super_=superCtor;ctor.prototype=Object.create(superCtor.prototype,{constructor:{value:ctor,enumerable:false,writable:true,configurable:true}});};}else{// old school shim for old browsers
module.exports=function inherits(ctor,superCtor){ctor.super_=superCtor;var TempCtor=function TempCtor(){};TempCtor.prototype=superCtor.prototype;ctor.prototype=new TempCtor();ctor.prototype.constructor=ctor;};}},{}],16:[function(require,module,exports){var assign=require('object-assign');var base64decode=require('./lib/base64decode');var hasWebAssembly=require('./lib/wa_detect');var DEFAULT_OPTIONS={js:true,wasm:true};function MultiMath(options){if(!(this instanceof MultiMath))return new MultiMath(options);var opts=assign({},DEFAULT_OPTIONS,options||{});this.options=opts;this.__cache={};this.has_wasm=hasWebAssembly();this.__init_promise=null;this.__modules=opts.modules||{};this.__memory=null;this.__wasm={};this.__isLE=new Uint32Array(new Uint8Array([1,0,0,0]).buffer)[0]===1;if(!this.options.js&&!this.options.wasm){throw new Error('mathlib: at least "js" or "wasm" should be enabled');}}MultiMath.prototype.use=function(module){this.__modules[module.name]=module;// Pin the best possible implementation
if(!this.has_wasm||!this.options.wasm||!module.wasm_fn){this[module.name]=module.fn;}else{this[module.name]=module.wasm_fn;}return this;};MultiMath.prototype.init=function(){if(this.__init_promise)return this.__init_promise;if(!this.options.js&&this.options.wasm&&!this.has_wasm){return Promise.reject(new Error('mathlib: only "wasm" was enabled, but it\'s not supported'));}var self=this;this.__init_promise=Promise.all(Object.keys(self.__modules).map(function(name){var module=self.__modules[name];if(!self.has_wasm||!self.options.wasm||!module.wasm_fn)return null;// If already compiled - exit
if(self.__wasm[name])return null;// Compile wasm source
return WebAssembly.compile(self.__base64decode(module.wasm_src)).then(function(m){self.__wasm[name]=m;});})).then(function(){return self;});return this.__init_promise;};////////////////////////////////////////////////////////////////////////////////
// Methods below are for internal use from plugins
// Simple decode base64 to typed array. Useful to load embedded webassembly
// code. You probably don't need to call this method directly.
//
MultiMath.prototype.__base64decode=base64decode;// Increase current memory to include specified number of bytes. Do nothing if
// size is already ok. You probably don't need to call this method directly,
// because it will be invoked from `.__instance()`.
//
MultiMath.prototype.__reallocate=function mem_grow_to(bytes){if(!this.__memory){this.__memory=new WebAssembly.Memory({initial:Math.ceil(bytes/(64*1024))});return this.__memory;}var mem_size=this.__memory.buffer.byteLength;if(mem_size<bytes){this.__memory.grow(Math.ceil((bytes-mem_size)/(64*1024)));}return this.__memory;};// Returns instantinated webassembly item by name, with specified memory size
// and environment.
// - use cache if available
// - do sync module init, if async init was not called earlier
// - allocate memory if not enougth
// - can export functions to webassembly via "env_extra",
//   for example, { exp: Math.exp }
//
MultiMath.prototype.__instance=function instance(name,memsize,env_extra){if(memsize)this.__reallocate(memsize);// If .init() was not called, do sync compile
if(!this.__wasm[name]){var module=this.__modules[name];this.__wasm[name]=new WebAssembly.Module(this.__base64decode(module.wasm_src));}if(!this.__cache[name]){var env_base={memoryBase:0,memory:this.__memory,tableBase:0,table:new WebAssembly.Table({initial:0,element:'anyfunc'})};this.__cache[name]=new WebAssembly.Instance(this.__wasm[name],{env:assign(env_base,env_extra||{})});}return this.__cache[name];};// Helper to calculate memory aligh for pointers. Webassembly does not require
// this, but you may wish to experiment. Default base = 8;
//
MultiMath.prototype.__align=function align(number,base){base=base||8;var reminder=number%base;return number+(reminder?base-reminder:0);};module.exports=MultiMath;},{"./lib/base64decode":17,"./lib/wa_detect":23,"object-assign":24}],17:[function(require,module,exports){var BASE64_MAP='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';module.exports=function base64decode(str){var input=str.replace(/[\r\n=]/g,''),// remove CR/LF & padding to simplify scan
max=input.length;var out=new Uint8Array(max*3>>2);// Collect by 6*4 bits (3 bytes)
var bits=0;var ptr=0;for(var idx=0;idx<max;idx++){if(idx%4===0&&idx){out[ptr++]=bits>>16&0xFF;out[ptr++]=bits>>8&0xFF;out[ptr++]=bits&0xFF;}bits=bits<<6|BASE64_MAP.indexOf(input.charAt(idx));}// Dump tail
var tailbits=max%4*6;if(tailbits===0){out[ptr++]=bits>>16&0xFF;out[ptr++]=bits>>8&0xFF;out[ptr++]=bits&0xFF;}else if(tailbits===18){out[ptr++]=bits>>10&0xFF;out[ptr++]=bits>>2&0xFF;}else if(tailbits===12){out[ptr++]=bits>>4&0xFF;}return out;};},{}],18:[function(require,module,exports){module.exports=function hsl_l16_js(img,width,height){var size=width*height;var out=new Uint16Array(size);var r,g,b,min,max;for(var i=0;i<size;i++){r=img[4*i];g=img[4*i+1];b=img[4*i+2];max=r>=g&&r>=b?r:g>=b&&g>=r?g:b;min=r<=g&&r<=b?r:g<=b&&g<=r?g:b;out[i]=(max+min)*257>>1;}return out;};},{}],19:[function(require,module,exports){module.exports={name:'unsharp_mask',fn:require('./unsharp_mask'),wasm_fn:require('./unsharp_mask_wasm'),wasm_src:require('./unsharp_mask_wasm_base64')};},{"./unsharp_mask":20,"./unsharp_mask_wasm":21,"./unsharp_mask_wasm_base64":22}],20:[function(require,module,exports){var glur_mono16=require('glur/mono16');var hsl_l16=require('./hsl_l16');module.exports=function unsharp(img,width,height,amount,radius,threshold){var r,g,b;var h,s,l;var min,max;var m1,m2,hShifted;var diff,iTimes4;if(amount===0||radius<0.5){return;}if(radius>2.0){radius=2.0;}var lightness=hsl_l16(img,width,height);var blured=new Uint16Array(lightness);// copy, because blur modify src
glur_mono16(blured,width,height,radius);var amountFp=amount/100*0x1000+0.5|0;var thresholdFp=threshold*257|0;var size=width*height;/* eslint-disable indent */for(var i=0;i<size;i++){diff=2*(lightness[i]-blured[i]);if(Math.abs(diff)>=thresholdFp){iTimes4=i*4;r=img[iTimes4];g=img[iTimes4+1];b=img[iTimes4+2];// convert RGB to HSL
// take RGB, 8-bit unsigned integer per each channel
// save HSL, H and L are 16-bit unsigned integers, S is 12-bit unsigned integer
// math is taken from here: http://www.easyrgb.com/index.php?X=MATH&H=18
// and adopted to be integer (fixed point in fact) for sake of performance
max=r>=g&&r>=b?r:g>=r&&g>=b?g:b;// min and max are in [0..0xff]
min=r<=g&&r<=b?r:g<=r&&g<=b?g:b;l=(max+min)*257>>1;// l is in [0..0xffff] that is caused by multiplication by 257
if(min===max){h=s=0;}else{s=l<=0x7fff?(max-min)*0xfff/(max+min)|0:(max-min)*0xfff/(2*0xff-max-min)|0;// s is in [0..0xfff]
// h could be less 0, it will be fixed in backward conversion to RGB, |h| <= 0xffff / 6
h=r===max?(g-b)*0xffff/(6*(max-min))|0:g===max?0x5555+((b-r)*0xffff/(6*(max-min))|0)// 0x5555 == 0xffff / 3
:0xaaaa+((r-g)*0xffff/(6*(max-min))|0);// 0xaaaa == 0xffff * 2 / 3
}// add unsharp mask mask to the lightness channel
l+=amountFp*diff+0x800>>12;if(l>0xffff){l=0xffff;}else if(l<0){l=0;}// convert HSL back to RGB
// for information about math look above
if(s===0){r=g=b=l>>8;}else{m2=l<=0x7fff?l*(0x1000+s)+0x800>>12:l+((0xffff-l)*s+0x800>>12);m1=2*l-m2>>8;m2>>=8;// save result to RGB channels
// R channel
hShifted=h+0x5555&0xffff;// 0x5555 == 0xffff / 3
r=hShifted>=0xaaaa?m1// 0xaaaa == 0xffff * 2 / 3
:hShifted>=0x7fff?m1+((m2-m1)*6*(0xaaaa-hShifted)+0x8000>>16):hShifted>=0x2aaa?m2// 0x2aaa == 0xffff / 6
:m1+((m2-m1)*6*hShifted+0x8000>>16);// G channel
hShifted=h&0xffff;g=hShifted>=0xaaaa?m1// 0xaaaa == 0xffff * 2 / 3
:hShifted>=0x7fff?m1+((m2-m1)*6*(0xaaaa-hShifted)+0x8000>>16):hShifted>=0x2aaa?m2// 0x2aaa == 0xffff / 6
:m1+((m2-m1)*6*hShifted+0x8000>>16);// B channel
hShifted=h-0x5555&0xffff;b=hShifted>=0xaaaa?m1// 0xaaaa == 0xffff * 2 / 3
:hShifted>=0x7fff?m1+((m2-m1)*6*(0xaaaa-hShifted)+0x8000>>16):hShifted>=0x2aaa?m2// 0x2aaa == 0xffff / 6
:m1+((m2-m1)*6*hShifted+0x8000>>16);}img[iTimes4]=r;img[iTimes4+1]=g;img[iTimes4+2]=b;}}};},{"./hsl_l16":18,"glur/mono16":14}],21:[function(require,module,exports){module.exports=function unsharp(img,width,height,amount,radius,threshold){if(amount===0||radius<0.5){return;}if(radius>2.0){radius=2.0;}var pixels=width*height;var img_bytes_cnt=pixels*4;var hsl_bytes_cnt=pixels*2;var blur_bytes_cnt=pixels*2;var blur_line_byte_cnt=Math.max(width,height)*4;// float32 array
var blur_coeffs_byte_cnt=8*4;// float32 array
var img_offset=0;var hsl_offset=img_bytes_cnt;var blur_offset=hsl_offset+hsl_bytes_cnt;var blur_tmp_offset=blur_offset+blur_bytes_cnt;var blur_line_offset=blur_tmp_offset+blur_bytes_cnt;var blur_coeffs_offset=blur_line_offset+blur_line_byte_cnt;var instance=this.__instance('unsharp_mask',img_bytes_cnt+hsl_bytes_cnt+blur_bytes_cnt*2+blur_line_byte_cnt+blur_coeffs_byte_cnt,{exp:Math.exp});// 32-bit copy is much faster in chrome
var img32=new Uint32Array(img.buffer);var mem32=new Uint32Array(this.__memory.buffer);mem32.set(img32);// HSL
var fn=instance.exports.hsl_l16||instance.exports._hsl_l16;fn(img_offset,hsl_offset,width,height);// BLUR
fn=instance.exports.blurMono16||instance.exports._blurMono16;fn(hsl_offset,blur_offset,blur_tmp_offset,blur_line_offset,blur_coeffs_offset,width,height,radius);// UNSHARP
fn=instance.exports.unsharp||instance.exports._unsharp;fn(img_offset,img_offset,hsl_offset,blur_offset,width,height,amount,threshold);// 32-bit copy is much faster in chrome
img32.set(new Uint32Array(this.__memory.buffer,0,pixels));};},{}],22:[function(require,module,exports){/* eslint-disable max-len */module.exports='AGFzbQEAAAABMQZgAXwBfGACfX8AYAZ/f39/f38AYAh/f39/f39/fQBgBH9/f38AYAh/f39/f39/fwACGQIDZW52A2V4cAAAA2VudgZtZW1vcnkCAAEDBgUBAgMEBQQEAXAAAAdMBRZfX2J1aWxkX2dhdXNzaWFuX2NvZWZzAAEOX19nYXVzczE2X2xpbmUAAgpibHVyTW9ubzE2AAMHaHNsX2wxNgAEB3Vuc2hhcnAABQkBAAqJEAXZAQEGfAJAIAFE24a6Q4Ia+z8gALujIgOaEAAiBCAEoCIGtjgCECABIANEAAAAAAAAAMCiEAAiBbaMOAIUIAFEAAAAAAAA8D8gBKEiAiACoiAEIAMgA6CiRAAAAAAAAPA/oCAFoaMiArY4AgAgASAEIANEAAAAAAAA8L+gIAKioiIHtjgCBCABIAQgA0QAAAAAAADwP6AgAqKiIgO2OAIIIAEgBSACoiIEtow4AgwgASACIAegIAVEAAAAAAAA8D8gBqGgIgKjtjgCGCABIAMgBKEgAqO2OAIcCwu3AwMDfwR9CHwCQCADKgIUIQkgAyoCECEKIAMqAgwhCyADKgIIIQwCQCAEQX9qIgdBAEgiCA0AIAIgAC8BALgiDSADKgIYu6IiDiAJuyIQoiAOIAq7IhGiIA0gAyoCBLsiEqIgAyoCALsiEyANoqCgoCIPtjgCACACQQRqIQIgAEECaiEAIAdFDQAgBCEGA0AgAiAOIBCiIA8iDiARoiANIBKiIBMgAC8BALgiDaKgoKAiD7Y4AgAgAkEEaiECIABBAmohACAGQX9qIgZBAUoNAAsLAkAgCA0AIAEgByAFbEEBdGogAEF+ai8BACIIuCINIAu7IhGiIA0gDLsiEqKgIA0gAyoCHLuiIg4gCrsiE6KgIA4gCbsiFKKgIg8gAkF8aioCALugqzsBACAHRQ0AIAJBeGohAiAAQXxqIQBBACAFQQF0ayEHIAEgBSAEQQF0QXxqbGohBgNAIAghAyAALwEAIQggBiANIBGiIAO4Ig0gEqKgIA8iECAToqAgDiAUoqAiDyACKgIAu6CrOwEAIAYgB2ohBiAAQX5qIQAgAkF8aiECIBAhDiAEQX9qIgRBAUoNAAsLCwvfAgIDfwZ8AkAgB0MAAAAAWw0AIARE24a6Q4Ia+z8gB0MAAAA/l7ujIgyaEAAiDSANoCIPtjgCECAEIAxEAAAAAAAAAMCiEAAiDraMOAIUIAREAAAAAAAA8D8gDaEiCyALoiANIAwgDKCiRAAAAAAAAPA/oCAOoaMiC7Y4AgAgBCANIAxEAAAAAAAA8L+gIAuioiIQtjgCBCAEIA0gDEQAAAAAAADwP6AgC6KiIgy2OAIIIAQgDiALoiINtow4AgwgBCALIBCgIA5EAAAAAAAA8D8gD6GgIgujtjgCGCAEIAwgDaEgC6O2OAIcIAYEQCAFQQF0IQogBiEJIAIhCANAIAAgCCADIAQgBSAGEAIgACAKaiEAIAhBAmohCCAJQX9qIgkNAAsLIAVFDQAgBkEBdCEIIAUhAANAIAIgASADIAQgBiAFEAIgAiAIaiECIAFBAmohASAAQX9qIgANAAsLC7wBAQV/IAMgAmwiAwRAQQAgA2shBgNAIAAoAgAiBEEIdiIHQf8BcSECAn8gBEH/AXEiAyAEQRB2IgRB/wFxIgVPBEAgAyIIIAMgAk8NARoLIAQgBCAHIAIgA0kbIAIgBUkbQf8BcQshCAJAIAMgAk0EQCADIAVNDQELIAQgByAEIAMgAk8bIAIgBUsbQf8BcSEDCyAAQQRqIQAgASADIAhqQYECbEEBdjsBACABQQJqIQEgBkEBaiIGDQALCwvTBgEKfwJAIAazQwAAgEWUQwAAyEKVu0QAAAAAAADgP6CqIQ0gBSAEbCILBEAgB0GBAmwhDgNAQQAgAi8BACADLwEAayIGQQF0IgdrIAcgBkEASBsgDk8EQCAAQQJqLQAAIQUCfyAALQAAIgYgAEEBai0AACIESSIJRQRAIAYiCCAGIAVPDQEaCyAFIAUgBCAEIAVJGyAGIARLGwshCAJ/IAYgBE0EQCAGIgogBiAFTQ0BGgsgBSAFIAQgBCAFSxsgCRsLIgogCGoiD0GBAmwiEEEBdiERQQAhDAJ/QQAiCSAIIApGDQAaIAggCmsiCUH/H2wgD0H+AyAIayAKayAQQYCABEkbbSEMIAYgCEYEQCAEIAVrQf//A2wgCUEGbG0MAQsgBSAGayAGIARrIAQgCEYiBhtB//8DbCAJQQZsbUHVqgFBqtUCIAYbagshCSARIAcgDWxBgBBqQQx1aiIGQQAgBkEAShsiBkH//wMgBkH//wNIGyEGAkACfwJAIAxB//8DcSIFBEAgBkH//wFKDQEgBUGAIGogBmxBgBBqQQx2DAILIAZBCHYiBiEFIAYhBAwCCyAFIAZB//8Dc2xBgBBqQQx2IAZqCyIFQQh2IQcgBkEBdCAFa0EIdiIGIQQCQCAJQdWqAWpB//8DcSIFQanVAksNACAFQf//AU8EQEGq1QIgBWsgByAGa2xBBmxBgIACakEQdiAGaiEEDAELIAchBCAFQanVAEsNACAFIAcgBmtsQQZsQYCAAmpBEHYgBmohBAsCfyAGIgUgCUH//wNxIghBqdUCSw0AGkGq1QIgCGsgByAGa2xBBmxBgIACakEQdiAGaiAIQf//AU8NABogByIFIAhBqdUASw0AGiAIIAcgBmtsQQZsQYCAAmpBEHYgBmoLIQUgCUGr1QJqQf//A3EiCEGp1QJLDQAgCEH//wFPBEBBqtUCIAhrIAcgBmtsQQZsQYCAAmpBEHYgBmohBgwBCyAIQanVAEsEQCAHIQYMAQsgCCAHIAZrbEEGbEGAgAJqQRB2IAZqIQYLIAEgBDoAACABQQFqIAU6AAAgAUECaiAGOgAACyADQQJqIQMgAkECaiECIABBBGohACABQQRqIQEgC0F/aiILDQALCwsL';},{}],23:[function(require,module,exports){var wa;module.exports=function hasWebAssembly(){// use cache if called before;
if(typeof wa!=='undefined')return wa;wa=false;if(typeof WebAssembly==='undefined')return wa;// If WebAssenbly is disabled, code can throw on compile
try{// https://github.com/brion/min-wasm-fail/blob/master/min-wasm-fail.in.js
// Additional check that WA internals are correct
/* eslint-disable comma-spacing, max-len */var bin=new Uint8Array([0,97,115,109,1,0,0,0,1,6,1,96,1,127,1,127,3,2,1,0,5,3,1,0,1,7,8,1,4,116,101,115,116,0,0,10,16,1,14,0,32,0,65,1,54,2,0,32,0,40,2,0,11]);var module=new WebAssembly.Module(bin);var instance=new WebAssembly.Instance(module,{});// test storing to and loading from a non-zero location via a parameter.
// Safari on iOS 11.2.5 returns 0 unexpectedly at non-zero locations
if(instance.exports.test(4)!==0)wa=true;return wa;}catch(__){}return wa;};},{}],24:[function(require,module,exports){/* eslint-disable no-unused-vars */var getOwnPropertySymbols=Object.getOwnPropertySymbols;var hasOwnProperty=Object.prototype.hasOwnProperty;var propIsEnumerable=Object.prototype.propertyIsEnumerable;function toObject(val){if(val===null||val===undefined){throw new TypeError('Object.assign cannot be called with null or undefined');}return Object(val);}function shouldUseNative(){try{if(!Object.assign){return false;}// Detect buggy property enumeration order in older V8 versions.
// https://bugs.chromium.org/p/v8/issues/detail?id=4118
var test1=new String('abc');// eslint-disable-line no-new-wrappers
test1[5]='de';if(Object.getOwnPropertyNames(test1)[0]==='5'){return false;}// https://bugs.chromium.org/p/v8/issues/detail?id=3056
var test2={};for(var i=0;i<10;i++){test2['_'+String.fromCharCode(i)]=i;}var order2=Object.getOwnPropertyNames(test2).map(function(n){return test2[n];});if(order2.join('')!=='0123456789'){return false;}// https://bugs.chromium.org/p/v8/issues/detail?id=3056
var test3={};'abcdefghijklmnopqrst'.split('').forEach(function(letter){test3[letter]=letter;});if(Object.keys(Object.assign({},test3)).join('')!=='abcdefghijklmnopqrst'){return false;}return true;}catch(err){// We don't expect any of the above to throw, but better to be safe.
return false;}}module.exports=shouldUseNative()?Object.assign:function(target,source){var from;var to=toObject(target);var symbols;for(var s=1;s<arguments.length;s++){from=Object(arguments[s]);for(var key in from){if(hasOwnProperty.call(from,key)){to[key]=from[key];}}if(getOwnPropertySymbols){symbols=getOwnPropertySymbols(from);for(var i=0;i<symbols.length;i++){if(propIsEnumerable.call(from,symbols[i])){to[symbols[i]]=from[symbols[i]];}}}}return to;};},{}],25:[function(require,module,exports){var bundleFn=arguments[3];var sources=arguments[4];var cache=arguments[5];var stringify=JSON.stringify;module.exports=function(fn,options){var wkey;var cacheKeys=Object.keys(cache);for(var i=0,l=cacheKeys.length;i<l;i++){var key=cacheKeys[i];var exp=cache[key].exports;// Using babel as a transpiler to use esmodule, the export will always
// be an object with the default export as a property of it. To ensure
// the existing api and babel esmodule exports are both supported we
// check for both
if(exp===fn||exp&&exp["default"]===fn){wkey=key;break;}}if(!wkey){wkey=Math.floor(Math.pow(16,8)*Math.random()).toString(16);var wcache={};for(var i=0,l=cacheKeys.length;i<l;i++){var key=cacheKeys[i];wcache[key]=key;}sources[wkey]=['function(require,module,exports){'+fn+'(self); }',wcache];}var skey=Math.floor(Math.pow(16,8)*Math.random()).toString(16);var scache={};scache[wkey]=wkey;sources[skey]=['function(require,module,exports){'+// try to call default if defined to also support babel esmodule exports
'var f = require('+stringify(wkey)+');'+'(f.default ? f.default : f)(self);'+'}',scache];var workerSources={};resolveSources(skey);function resolveSources(key){workerSources[key]=true;for(var depPath in sources[key][1]){var depKey=sources[key][1][depPath];if(!workerSources[depKey]){resolveSources(depKey);}}}var src='('+bundleFn+')({'+Object.keys(workerSources).map(function(key){return stringify(key)+':['+sources[key][0]+','+stringify(sources[key][1])+']';}).join(',')+'},{},['+stringify(skey)+'])';var URL=window.URL||window.webkitURL||window.mozURL||window.msURL;var blob=new Blob([src],{type:'text/javascript'});if(options&&options.bare){return blob;}var workerUrl=URL.createObjectURL(blob);var worker=new Worker(workerUrl);worker.objectURL=workerUrl;return worker;};},{}],"/":[function(require,module,exports){var _slicedToArray=function(){function sliceIterator(arr,i){var _arr=[];var _n=true;var _d=false;var _e=undefined;try{for(var _i=arr[Symbol.iterator](),_s;!(_n=(_s=_i.next()).done);_n=true){_arr.push(_s.value);if(i&&_arr.length===i)break;}}catch(err){_d=true;_e=err;}finally{try{if(!_n&&_i["return"])_i["return"]();}finally{if(_d)throw _e;}}return _arr;}return function(arr,i){if(Array.isArray(arr)){return arr;}else if(Symbol.iterator in Object(arr)){return sliceIterator(arr,i);}else{throw new TypeError("Invalid attempt to destructure non-iterable instance");}};}();var assign=require('object-assign');var webworkify=require('webworkify');var MathLib=require('./lib/mathlib');var Pool=require('./lib/pool');var utils=require('./lib/utils');var worker=require('./lib/worker');var createStages=require('./lib/stepper');var createRegions=require('./lib/tiler');// Deduplicate pools & limiters with the same configs
// when user creates multiple pica instances.
var singletones={};var NEED_SAFARI_FIX=false;try{if(typeof navigator!=='undefined'&&navigator.userAgent){NEED_SAFARI_FIX=navigator.userAgent.indexOf('Safari')>=0;}}catch(e){}var concurrency=1;if(typeof navigator!=='undefined'){concurrency=Math.min(navigator.hardwareConcurrency||1,4);}var DEFAULT_PICA_OPTS={tile:1024,concurrency:concurrency,features:['js','wasm','ww'],idle:2000};var DEFAULT_RESIZE_OPTS={quality:3,alpha:false,unsharpAmount:0,unsharpRadius:0.0,unsharpThreshold:0};var CAN_NEW_IMAGE_DATA=void 0;var CAN_CREATE_IMAGE_BITMAP=void 0;function workerFabric(){return{value:webworkify(worker),destroy:function destroy(){this.value.terminate();if(typeof window!=='undefined'){var url=window.URL||window.webkitURL||window.mozURL||window.msURL;if(url&&url.revokeObjectURL&&this.value.objectURL){url.revokeObjectURL(this.value.objectURL);}}}};}////////////////////////////////////////////////////////////////////////////////
// API methods
function Pica(options){if(!(this instanceof Pica))return new Pica(options);this.options=assign({},DEFAULT_PICA_OPTS,options||{});var limiter_key='lk_'+this.options.concurrency;// Share limiters to avoid multiple parallel workers when user creates
// multiple pica instances.
this.__limit=singletones[limiter_key]||utils.limiter(this.options.concurrency);if(!singletones[limiter_key])singletones[limiter_key]=this.__limit;// List of supported features, according to options & browser/node.js
this.features={js:false,// pure JS implementation, can be disabled for testing
wasm:false,// webassembly implementation for heavy functions
cib:false,// resize via createImageBitmap (only FF at this moment)
ww:false// webworkers
};this.__workersPool=null;// Store requested features for webworkers
this.__requested_features=[];this.__mathlib=null;}Pica.prototype.init=function(){var _this=this;if(this.__initPromise)return this.__initPromise;// Test if we can create ImageData without canvas and memory copy
if(CAN_NEW_IMAGE_DATA!==false&&CAN_NEW_IMAGE_DATA!==true){CAN_NEW_IMAGE_DATA=false;if(typeof ImageData!=='undefined'&&typeof Uint8ClampedArray!=='undefined'){try{/* eslint-disable no-new */new ImageData(new Uint8ClampedArray(400),10,10);CAN_NEW_IMAGE_DATA=true;}catch(__){}}}// ImageBitmap can be effective in 2 places:
//
// 1. Threaded jpeg unpack (basic)
// 2. Built-in resize (blocked due problem in chrome, see issue #89)
//
// For basic use we also need ImageBitmap wo support .close() method,
// see https://developer.mozilla.org/ru/docs/Web/API/ImageBitmap
if(CAN_CREATE_IMAGE_BITMAP!==false&&CAN_CREATE_IMAGE_BITMAP!==true){CAN_CREATE_IMAGE_BITMAP=false;if(typeof ImageBitmap!=='undefined'){if(ImageBitmap.prototype&&ImageBitmap.prototype.close){CAN_CREATE_IMAGE_BITMAP=true;}else{this.debug('ImageBitmap does not support .close(), disabled');}}}var features=this.options.features.slice();if(features.indexOf('all')>=0){features=['cib','wasm','js','ww'];}this.__requested_features=features;this.__mathlib=new MathLib(features);// Check WebWorker support if requested
if(features.indexOf('ww')>=0){if(typeof window!=='undefined'&&'Worker'in window){// IE <= 11 don't allow to create webworkers from string. We should check it.
// https://connect.microsoft.com/IE/feedback/details/801810/web-workers-from-blob-urls-in-ie-10-and-11
try{var wkr=require('webworkify')(function(){});wkr.terminate();this.features.ww=true;// pool uniqueness depends on pool config + webworker config
var wpool_key='wp_'+JSON.stringify(this.options);if(singletones[wpool_key]){this.__workersPool=singletones[wpool_key];}else{this.__workersPool=new Pool(workerFabric,this.options.idle);singletones[wpool_key]=this.__workersPool;}}catch(__){}}}var initMath=this.__mathlib.init().then(function(mathlib){// Copy detected features
assign(_this.features,mathlib.features);});var checkCibResize=void 0;if(!CAN_CREATE_IMAGE_BITMAP){checkCibResize=Promise.resolve(false);}else{checkCibResize=utils.cib_support().then(function(status){if(_this.features.cib&&features.indexOf('cib')<0){_this.debug('createImageBitmap() resize supported, but disabled by config');return;}if(features.indexOf('cib')>=0)_this.features.cib=status;});}// Init math lib. That's async because can load some
this.__initPromise=Promise.all([initMath,checkCibResize]).then(function(){return _this;});return this.__initPromise;};Pica.prototype.resize=function(from,to,options){var _this2=this;this.debug('Start resize...');var opts=assign({},DEFAULT_RESIZE_OPTS);if(!isNaN(options)){opts=assign(opts,{quality:options});}else if(options){opts=assign(opts,options);}opts.toWidth=to.width;opts.toHeight=to.height;opts.width=from.naturalWidth||from.width;opts.height=from.naturalHeight||from.height;// Prevent stepper from infinite loop
if(to.width===0||to.height===0){return Promise.reject(new Error('Invalid output size: '+to.width+'x'+to.height));}if(opts.unsharpRadius>2)opts.unsharpRadius=2;var canceled=false;var cancelToken=null;if(opts.cancelToken){// Wrap cancelToken to avoid successive resolve & set flag
cancelToken=opts.cancelToken.then(function(data){canceled=true;throw data;},function(err){canceled=true;throw err;});}var DEST_TILE_BORDER=3;// Max possible filter window size
var destTileBorder=Math.ceil(Math.max(DEST_TILE_BORDER,2.5*opts.unsharpRadius|0));return this.init().then(function(){if(canceled)return cancelToken;// if createImageBitmap supports resize, just do it and return
if(_this2.features.cib){var toCtx=to.getContext('2d',{alpha:Boolean(opts.alpha)});_this2.debug('Resize via createImageBitmap()');return createImageBitmap(from,{resizeWidth:opts.toWidth,resizeHeight:opts.toHeight,resizeQuality:utils.cib_quality_name(opts.quality)}).then(function(imageBitmap){if(canceled)return cancelToken;// if no unsharp - draw directly to output canvas
if(!opts.unsharpAmount){toCtx.drawImage(imageBitmap,0,0);imageBitmap.close();toCtx=null;_this2.debug('Finished!');return to;}_this2.debug('Unsharp result');var tmpCanvas=document.createElement('canvas');tmpCanvas.width=opts.toWidth;tmpCanvas.height=opts.toHeight;var tmpCtx=tmpCanvas.getContext('2d',{alpha:Boolean(opts.alpha)});tmpCtx.drawImage(imageBitmap,0,0);imageBitmap.close();var iData=tmpCtx.getImageData(0,0,opts.toWidth,opts.toHeight);_this2.__mathlib.unsharp(iData.data,opts.toWidth,opts.toHeight,opts.unsharpAmount,opts.unsharpRadius,opts.unsharpThreshold);toCtx.putImageData(iData,0,0);iData=tmpCtx=tmpCanvas=toCtx=null;_this2.debug('Finished!');return to;});}//
// No easy way, let's resize manually via arrays
//
// Share cache between calls:
//
// - wasm instance
// - wasm memory object
//
var cache={};// Call resizer in webworker or locally, depending on config
var invokeResize=function invokeResize(opts){return Promise.resolve().then(function(){if(!_this2.features.ww)return _this2.__mathlib.resizeAndUnsharp(opts,cache);return new Promise(function(resolve,reject){var w=_this2.__workersPool.acquire();if(cancelToken)cancelToken["catch"](function(err){return reject(err);});w.value.onmessage=function(ev){w.release();if(ev.data.err)reject(ev.data.err);else resolve(ev.data.result);};w.value.postMessage({opts:opts,features:_this2.__requested_features,preload:{wasm_nodule:_this2.__mathlib.__}},[opts.src.buffer]);});});};var tileAndResize=function tileAndResize(from,to,opts){var srcCtx=void 0;var srcImageBitmap=void 0;var toCtx=void 0;var processTile=function processTile(tile){return _this2.__limit(function(){if(canceled)return cancelToken;var srcImageData=void 0;// Extract tile RGBA buffer, depending on input type
if(utils.isCanvas(from)){_this2.debug('Get tile pixel data');// If input is Canvas - extract region data directly
srcImageData=srcCtx.getImageData(tile.x,tile.y,tile.width,tile.height);}else{// If input is Image or decoded to ImageBitmap,
// draw region to temporary canvas and extract data from it
//
// Note! Attempt to reuse this canvas causes significant slowdown in chrome
//
_this2.debug('Draw tile imageBitmap/image to temporary canvas');var tmpCanvas=document.createElement('canvas');tmpCanvas.width=tile.width;tmpCanvas.height=tile.height;var tmpCtx=tmpCanvas.getContext('2d',{alpha:Boolean(opts.alpha)});tmpCtx.globalCompositeOperation='copy';tmpCtx.drawImage(srcImageBitmap||from,tile.x,tile.y,tile.width,tile.height,0,0,tile.width,tile.height);_this2.debug('Get tile pixel data');srcImageData=tmpCtx.getImageData(0,0,tile.width,tile.height);tmpCtx=tmpCanvas=null;}var o={src:srcImageData.data,width:tile.width,height:tile.height,toWidth:tile.toWidth,toHeight:tile.toHeight,scaleX:tile.scaleX,scaleY:tile.scaleY,offsetX:tile.offsetX,offsetY:tile.offsetY,quality:opts.quality,alpha:opts.alpha,unsharpAmount:opts.unsharpAmount,unsharpRadius:opts.unsharpRadius,unsharpThreshold:opts.unsharpThreshold};_this2.debug('Invoke resize math');return Promise.resolve().then(function(){return invokeResize(o);}).then(function(result){if(canceled)return cancelToken;srcImageData=null;var toImageData=void 0;_this2.debug('Convert raw rgba tile result to ImageData');if(CAN_NEW_IMAGE_DATA){// this branch is for modern browsers
// If `new ImageData()` & Uint8ClampedArray suported
toImageData=new ImageData(new Uint8ClampedArray(result),tile.toWidth,tile.toHeight);}else{// fallback for `node-canvas` and old browsers
// (IE11 has ImageData but does not support `new ImageData()`)
toImageData=toCtx.createImageData(tile.toWidth,tile.toHeight);if(toImageData.data.set){toImageData.data.set(result);}else{// IE9 don't have `.set()`
for(var i=toImageData.data.length-1;i>=0;i--){toImageData.data[i]=result[i];}}}_this2.debug('Draw tile');if(NEED_SAFARI_FIX){// Safari draws thin white stripes between tiles without this fix
toCtx.putImageData(toImageData,tile.toX,tile.toY,tile.toInnerX-tile.toX,tile.toInnerY-tile.toY,tile.toInnerWidth+1e-5,tile.toInnerHeight+1e-5);}else{toCtx.putImageData(toImageData,tile.toX,tile.toY,tile.toInnerX-tile.toX,tile.toInnerY-tile.toY,tile.toInnerWidth,tile.toInnerHeight);}return null;});});};// Need to normalize data source first. It can be canvas or image.
// If image - try to decode in background if possible
return Promise.resolve().then(function(){toCtx=to.getContext('2d',{alpha:Boolean(opts.alpha)});if(utils.isCanvas(from)){srcCtx=from.getContext('2d',{alpha:Boolean(opts.alpha)});return null;}if(utils.isImage(from)){// try do decode image in background for faster next operations
if(!CAN_CREATE_IMAGE_BITMAP)return null;_this2.debug('Decode image via createImageBitmap');return createImageBitmap(from).then(function(imageBitmap){srcImageBitmap=imageBitmap;});}throw new Error('".from" should be image or canvas');}).then(function(){if(canceled)return cancelToken;_this2.debug('Calculate tiles');//
// Here we are with "normalized" source,
// follow to tiling
//
var regions=createRegions({width:opts.width,height:opts.height,srcTileSize:_this2.options.tile,toWidth:opts.toWidth,toHeight:opts.toHeight,destTileBorder:destTileBorder});var jobs=regions.map(function(tile){return processTile(tile);});function cleanup(){if(srcImageBitmap){srcImageBitmap.close();srcImageBitmap=null;}}_this2.debug('Process tiles');return Promise.all(jobs).then(function(){_this2.debug('Finished!');cleanup();return to;},function(err){cleanup();throw err;});});};var processStages=function processStages(stages,from,to,opts){if(canceled)return cancelToken;var _stages$shift=stages.shift(),_stages$shift2=_slicedToArray(_stages$shift,2),toWidth=_stages$shift2[0],toHeight=_stages$shift2[1];var isLastStage=stages.length===0;opts=assign({},opts,{toWidth:toWidth,toHeight:toHeight,// only use user-defined quality for the last stage,
// use simpler (Hamming) filter for the first stages where
// scale factor is large enough (more than 2-3)
quality:isLastStage?opts.quality:Math.min(1,opts.quality)});var tmpCanvas=void 0;if(!isLastStage){// create temporary canvas
tmpCanvas=document.createElement('canvas');tmpCanvas.width=toWidth;tmpCanvas.height=toHeight;}return tileAndResize(from,isLastStage?to:tmpCanvas,opts).then(function(){if(isLastStage)return to;opts.width=toWidth;opts.height=toHeight;return processStages(stages,tmpCanvas,to,opts);});};var stages=createStages(opts.width,opts.height,opts.toWidth,opts.toHeight,_this2.options.tile,destTileBorder);return processStages(stages,from,to,opts);});};// RGBA buffer resize
//
Pica.prototype.resizeBuffer=function(options){var _this3=this;var opts=assign({},DEFAULT_RESIZE_OPTS,options);return this.init().then(function(){return _this3.__mathlib.resizeAndUnsharp(opts);});};Pica.prototype.toBlob=function(canvas,mimeType,quality){mimeType=mimeType||'image/png';return new Promise(function(resolve){if(canvas.toBlob){canvas.toBlob(function(blob){return resolve(blob);},mimeType,quality);return;}// Fallback for old browsers
var asString=atob(canvas.toDataURL(mimeType,quality).split(',')[1]);var len=asString.length;var asBuffer=new Uint8Array(len);for(var i=0;i<len;i++){asBuffer[i]=asString.charCodeAt(i);}resolve(new Blob([asBuffer],{type:mimeType}));});};Pica.prototype.debug=function(){};module.exports=Pica;},{"./lib/mathlib":1,"./lib/pool":9,"./lib/stepper":10,"./lib/tiler":11,"./lib/utils":12,"./lib/worker":13,"object-assign":24,"webworkify":25}]},{},[])("/");});});/* eslint-disable camelcase, no-multi-spaces, no-mixed-operators, func-names */function error(message,code){var err=new Error(message);err.code=code;return err;}/* eslint-disable no-bitwise */function Exif(data){this.data=data;var sig=String.fromCharCode.apply(null,data.subarray(0,4));if(sig!=='II\x2A\0'&&sig!=='MM\0\x2A'){throw error('invalid TIFF signature','EBADDATA');}this.big_endian=sig[0]==='M';}Exif.prototype.readUInt16=function(buffer,offset){if(offset+2>buffer.length)throw error('unexpected EOF','EBADDATA');return this.big_endian?buffer[offset]*0x100+buffer[offset+1]:buffer[offset]+buffer[offset+1]*0x100;};Exif.prototype.readUInt32=function(buffer,offset){if(offset+4>buffer.length)throw error('unexpected EOF','EBADDATA');return this.big_endian?buffer[offset]*0x1000000+buffer[offset+1]*0x10000+buffer[offset+2]*0x100+buffer[offset+3]:buffer[offset]+buffer[offset+1]*0x100+buffer[offset+2]*0x10000+buffer[offset+3]*0x1000000;};Exif.prototype.writeUInt16=function(buffer,data,offset){// this could happen if TIFF is hand-crafted to be smaller than sum of its entries,
// and we wrongly allocate a smaller buffer than necessary
if(offset+2>buffer.length)throw error('TIFF data is too large','EBADDATA');if(this.big_endian){buffer[offset]=data>>>8&0xFF;buffer[offset+1]=data&0xFF;}else{buffer[offset]=data&0xFF;buffer[offset+1]=data>>>8&0xFF;}};Exif.prototype.writeUInt32=function(buffer,data,offset){// this could happen if TIFF is hand-crafted to be smaller than sum of its entries,
// and we wrongly allocate a smaller buffer than necessary
if(offset+4>buffer.length)throw error('TIFF data is too large','EBADDATA');if(this.big_endian){buffer[offset]=data>>>24&0xFF;buffer[offset+1]=data>>>16&0xFF;buffer[offset+2]=data>>>8&0xFF;buffer[offset+3]=data&0xFF;}else{buffer[offset]=data&0xFF;buffer[offset+1]=data>>>8&0xFF;buffer[offset+2]=data>>>16&0xFF;buffer[offset+3]=data>>>24&0xFF;}};// Filter exif data and write it into a buffer
//
// - options: Object
//   - maxEntrySize: Number
//   - onIFDEntry: Function
// - out: Uint8Array - a buffer to write exif to
//
// `maxEntrySize` meaning: when filtering Exif, remove all fields with size
// more than `maxEntrySize`. Note that most useful data in Exif is stored as
// integers (<= 12 bytes), so setting it higher will only filter out comments
// and vendor crap (default: 100 bytes)
//
// `onIFDEntry` is called on each entry inside IFD (think about Exif being
// an array of IFDs, and IFD being an array of entries). You may return `false`
// to filter out said element from Exif.
//
// `onIFDEntry` function signature:
//
//  - ifd: Number
//    - 0x0000 for IFD0
//    - 0x0001 for IFD1 (not currently supported)
//    - 0x8825 for GPSIFD
//    - 0x8769 for ExifIFD
//    - 0xA005 for InteropIFD
//
//  - entry: Object - the contents of the IFD entry
//    - tag:   Number - data tag
//    - type:  Number - data type (short, double, ascii, etc., see details in TIFF spec)
//    - count: Number - an amount of items in buffer (see details in TIFF spec)
//    - value: Buffer - data buffer (use this.readUint16 or this.readUint32 to read from there)
//
// Example:
//
// ```js
// onIFDEntry: function readOrientation(ifd, entry) {
//   if (ifd === 0 && entry.tag === 0x112 && entry.type === 3) {
//     console.log('Orientation =', this.readUInt16(entry.value, 0));
//   }
// }
// ```
//
Exif.prototype.filter=function(options,out){var _this20=this;var maxSize=options&&options.maxEntrySize?options.maxEntrySize:100;/* eslint-disable func-style */var filter_entry=function filter_entry(sectionName){return function(entry){if(options&&options.onIFDEntry){if(options.onIFDEntry.call(_this20,sectionName,entry)===false){return false;}}return entry.value.length<=maxSize;};};this.output={buf:out,length:0};var offset=0;// copy signature (it's already checked on init)
this.output.buf[0]=this.data[0];this.output.buf[1]=this.data[1];this.output.buf[2]=this.data[2];this.output.buf[3]=this.data[3];this.output.length+=4;this.writeUInt32(this.output.buf,8,this.output.length);this.output.length+=4;offset=this.readUInt32(this.data,4);// We only do read IFD0 here, IFD1 is ignored
// because we don't need to preserve thumbnails
//
var t=this.processIFDSection(offset,filter_entry(0));t.entries.forEach(function(entry){//                ExifIFD                 GPSIFD                interopIFD
if(entry.tag===0x8769||entry.tag===0x8825||entry.tag===0xA005){if(entry.type===4){_this20.writeUInt32(_this20.output.buf,_this20.output.length,entry.written_offset+8);var off=_this20.readUInt32(entry.value,0);_this20.processIFDSection(off,filter_entry(entry.tag));}}});// we wrote more data than we allocated buffer for,
// this could happen if TIFF is hand-crafted to be smaller than sum of its entries
//
if(this.output.length>this.output.buf.length){throw error('TIFF data is too large','EBADDATA');}return this.output.length;};Exif.prototype.readIFDEntry=function(offset){var tag=this.readUInt16(this.data,offset);var type=this.readUInt16(this.data,offset+2);var count=this.readUInt32(this.data,offset+4);var unit_length;switch(type){case 1:// byte
case 2:// ascii
case 6:// sbyte
case 7:// undefined
unit_length=1;break;case 3:// short
case 8:// sshort
unit_length=2;break;case 4:// long
case 9:// slong
case 11:// float
unit_length=4;break;case 5:// rational
case 10:// srational
case 12:// double
unit_length=8;break;default:// unknown type, skipping
return;}var value;var length=unit_length*count;if(length<=4){value=this.data.subarray(offset+8,offset+12);if(value.length<4)throw error('unexpected EOF','EBADDATA');}else{var offv=this.readUInt32(this.data,offset+8);value=this.data.subarray(offv,offv+length);if(value.length<length)throw error('unexpected EOF','EBADDATA');}return{tag:tag,type:type,count:count,value:value};// eslint-disable-line consistent-return
};Exif.prototype.processIFDSection=function(offset,filter){var _this21=this;var entries_to_write=[];var entries_count=this.readUInt16(this.data,offset);offset+=2;for(var i=0;i<entries_count;i+=1){var entry=this.readIFDEntry(offset+i*12);/* eslint-disable no-continue */if(!entry)continue;if(!filter(entry))continue;entries_to_write.push(entry);}this.writeUInt16(this.output.buf,entries_to_write.length,this.output.length);this.output.length+=2;var written_ifb_offset=this.output.length;entries_to_write.forEach(function(entry){entry.written_offset=_this21.output.length;_this21.writeUInt16(_this21.output.buf,entry.tag,_this21.output.length);_this21.writeUInt16(_this21.output.buf,entry.type,_this21.output.length+2);_this21.writeUInt32(_this21.output.buf,entry.count,_this21.output.length+4);if(entry.value.length<=4){if(entry.value.length+_this21.output.length+8>_this21.output.buf.length){throw error('TIFF data is too large','EBADDATA');}_this21.output.buf.set(entry.value,_this21.output.length+8);}_this21.output.length+=12;});this.writeUInt32(this.output.buf,0,this.output.length);this.output.length+=4;entries_to_write.forEach(function(entry,i){if(entry.value.length>4){_this21.writeUInt32(_this21.output.buf,_this21.output.length,written_ifb_offset+i*12+8);if(entry.value.length+_this21.output.length>_this21.output.buf.length){throw error('TIFF data is too large','EBADDATA');}_this21.output.buf.set(entry.value,_this21.output.length);_this21.output.length+=entry.value.length;if(_this21.output.length%2){// ensure that everything is at word boundary
_this21.output.buf[_this21.output.length]=0xFF;_this21.output.length+=1;}}});return{entries:entries_to_write,next_ifb:this.readUInt32(this.data,offset+entries_count*12)};};var filterExif=function filterExif(data,options){if(String.fromCharCode.apply(null,data.subarray(0,6))!=='Exif\0\0'){throw error('invalid Exif signature','ENOTEXIF');}// Create buffer of the same length as input.
//
// This is good enough for most of the cases, but will throw
// if exif is packed (referencing the same data multiple times)
//
var output=new data.constructor(data.length);var exif=new Exif(data.subarray(6));'Exif\0\0'.split('').forEach(function(c,pos){output[pos]=c.charCodeAt(0);});// Write filtered exif into output at position 6,
// it's built around the fact that subarray copy is shallow
//
var length=exif.filter(options,output.subarray(6));return new data.constructor(output.subarray(0,length+6));};/* eslint-disable */ // Parser states
var FILE_START=0;// start of the file, read signature (FF)
var FILE_START_FF=1;// start of the file, read signature (D8)
var SEGMENT_START=2;// start of a segment, expect to read FF
var SEGMENT_MARKER=3;// read marker ID
var SEGMENT_LENGTH=4;// read segment length (2 bytes total)
var SEGMENT_IGNORE=5;// read segment and ignore it
var SEGMENT_PIPE=6;// read segment and pass it into output
var SEGMENT_PIPE_DATA=7;// read segment and pass it into output (data)
var SEGMENT_BUFFER=8;// buffer segment, process as exif
var SEGMENT_BUFFER_DATA=9;// buffer segment, process as exif
var IMAGE=10;// start reading image
var IMAGE_FF=11;// process possible segment inside image
var FINAL=12;// ignore the rest of the data
/* eslint-disable no-bitwise */function JpegFilter(options){if(!(this instanceof JpegFilter))return new JpegFilter(options);options=options||{};this.output=[];this._state=FILE_START;//
// Parser options
//
// remove ICC profile (2-10 kB)
this._removeICCandAPP=options.removeICCandAPP;// `true` - remove Exif completely, `false` - filter it and remove thumbnail
this._removeExif=options.removeExif;// remove other meta data (XMP, Photoshop, etc.)
this._filter=options.filter;// remove JPEG COM segments
this._removeComments=options.removeComments;// remove the rest of the image (everything except metadata);
// if it's `true`, output will be a series of segments, and NOT a valid jpeg
this._removeImage=options.removeImage;// add a comment at the beginning of the JPEG
// (it's added after JFIF, but before anything else)
this._comment=options.comment;// exif options (passed for exif parser as is)
this._maxEntrySize=options.maxEntrySize;this._onIFDEntry=options.onIFDEntry;// internal data
this._markerCode=0;this._bytesLeft=0;this._segmentLength=0;this._app1buffer=null;this._app1pos=0;this._bytesRead=0;//
this._BufferConstructor=null;this._bufferUseAlloc=false;this._bufferUseFrom=false;}function toHex(number){var n=number.toString(16).toUpperCase();for(var i=2-n.length;i>0;i-=1){n="0".concat(n);}return"0x".concat(n);}// Perform a shallow copy of a buffer or typed array
//
function slice(buf,start,end){if(buf.slice&&buf.copy&&buf.writeDoubleBE){//
// Looks like node.js buffer
//
// - we use buf.slice() in node.js buffers because
//   buf.subarray() is not a buffer
//
// - we use buf.subarray() in uint8arrays because
//   buf.slice() is not a shallow copy
//
return buf.slice(start,end);}return buf.subarray(start,end);}// Copy one buffer to another
//
function copy(src,dst,dst_offset){if(src.length+dst_offset>dst.length)throw new Error('buffer is too small');if(src.copy){src.copy(dst,dst_offset);}else{dst.set(src,dst_offset);}}JpegFilter.prototype._error=function(message,code){// double error?
if(this._state===FINAL)return;var err=new Error(message);err.code=code;this._state=FINAL;this.onError(err);};// Detect required output type by first input chunk
JpegFilter.prototype._detectBuffer=function(data){if(this._BufferConstructor)return;this._BufferConstructor=data.constructor;this._bufferUseAlloc=typeof data.constructor.alloc==='function';this._bufferUseFrom=typeof data.constructor.from==='function';};// Helper to allocate output with proper class type (Uint8Array|Buffer)
// All this magic is required only to make code work in browser too.
JpegFilter.prototype._buffer=function(arg){var cls=this._BufferConstructor;/* eslint-disable new-cap */if(typeof arg==='number'){return this._bufferUseAlloc?cls.alloc(arg):new cls(arg);}return this._bufferUseFrom?cls.from(arg):new cls(arg);};/* eslint-disable max-depth */JpegFilter.prototype.push=function(data){var buf;var di;var i=0;// guess output datd type by first input chunk
this._detectBuffer(data);while(i<data.length){var b=data[i];switch(this._state){// eslint-disable-line
// start of the file, read signature (FF)
case FILE_START:if(b!==0xFF){this._error('unknown file format','ENOTJPEG',i);return;}this._state=FILE_START_FF;i+=1;break;// start of the file, read signature (D8)
case FILE_START_FF:if(b!==0xD8){this._error('unknown file format','ENOTJPEG',i);return;}this.onData(this._buffer([0xFF,0xD8]));this._state=SEGMENT_START;i+=1;break;// start of a segment, expect to read FF
case SEGMENT_START:if(this._markerCode===0xDA){// previous segment was SOS, so we should read image data instead
this._state=IMAGE;break;}if(b!==0xFF){this._error("unexpected byte at segment start: ".concat(toHex(b),"\n          (offset ").concat(toHex(this._bytesRead+i)," )"),'EBADDATA');return;}this._state=SEGMENT_MARKER;i+=1;break;// read marker ID
/* eslint-disable yoda */case SEGMENT_MARKER:// standalone markers, according to JPEG 1992,
// http://www.w3.org/Graphics/JPEG/itu-t81.pdf, see Table B.1
if(0xD0<=b&&b<=0xD9||b===0x01){this._markerCode=b;this._bytesLeft=0;this._segmentLength=0;if(this._markerCode===0xD9/* EOI */){this.onData(this._buffer([0xFF,0xD9]));this._state=FINAL;this.onEnd();}else{this._state=SEGMENT_LENGTH;}i+=1;break;}// the rest of the unreserved markers
if(0xC0<=b&&b<=0xFE){this._markerCode=b;this._bytesLeft=2;this._segmentLength=0;this._state=SEGMENT_LENGTH;i+=1;break;}if(b===0xFF){// padding byte, skip it
i+=1;break;}// unknown markers
this._error("unknown marker: ".concat(toHex(b),"\n          (offset ").concat(toHex(this._bytesRead+i)," )"),'EBADDATA');return;// return after error, not break
// read segment length (2 bytes total)
case SEGMENT_LENGTH:while(this._bytesLeft>0&&i<data.length){this._segmentLength=this._segmentLength*0x100+data[i];this._bytesLeft-=1;i+=1;}if(this._bytesLeft<=0){if(this._comment!==null&&typeof this._comment!=='undefined'&&this._markerCode!==0xE0){// insert comment field before any other markers (except APP0)
//
// (we can insert it anywhere, but JFIF segment being first
// looks nicer in hexdump)
//
var enc=void 0;try{// poor man's utf8 encoding
enc=unescape(encodeURIComponent(this._comment));}catch(err){enc=this._comment;}buf=this._buffer(5+enc.length);buf[0]=0xFF;buf[1]=0xFE;buf[2]=enc.length+3>>>8&0xFF;buf[3]=enc.length+3&0xFF;/* eslint-disable no-loop-func */enc.split('').forEach(function(c,pos){buf[pos+4]=c.charCodeAt(0)&0xFF;});buf[buf.length-1]=0;this._comment=null;this.onData(buf);}if(this._markerCode===0xE0){// APP0, 14-byte JFIF header
this._state=SEGMENT_PIPE;}else if(this._markerCode===0xE1){// APP1, Exif candidate
this._state=this._filter&&this._removeExif?SEGMENT_IGNORE:// ignore if we remove both
SEGMENT_BUFFER;}else if(this._markerCode===0xE2||this._markerCode===0xEE){// APP2, ICC_profile, APP14
this._state=this._removeICCandAPP?SEGMENT_IGNORE:SEGMENT_PIPE;}else if(this._markerCode>0xE2&&this._markerCode<0xF0){// Photoshop metadata, etc.
this._state=this._filter?SEGMENT_IGNORE:SEGMENT_PIPE;}else if(this._markerCode===0xFE){// Comments
this._state=this._removeComments?SEGMENT_IGNORE:SEGMENT_PIPE;}else{// other valid headers
this._state=this._removeImage?SEGMENT_IGNORE:SEGMENT_PIPE;}this._bytesLeft=Math.max(this._segmentLength-2,0);}break;// read segment and ignore it
case SEGMENT_IGNORE:di=Math.min(this._bytesLeft,data.length-i);i+=di;this._bytesLeft-=di;if(this._bytesLeft<=0)this._state=SEGMENT_START;break;// read segment and pass it into output
case SEGMENT_PIPE:if(this._bytesLeft<=0){this._state=SEGMENT_START;}else{this._state=SEGMENT_PIPE_DATA;}buf=this._buffer(4);buf[0]=0xFF;buf[1]=this._markerCode;buf[2]=this._bytesLeft+2>>>8&0xFF;buf[3]=this._bytesLeft+2&0xFF;this.onData(buf);break;// read segment and pass it into output
case SEGMENT_PIPE_DATA:di=Math.min(this._bytesLeft,data.length-i);this.onData(slice(data,i,i+di));i+=di;this._bytesLeft-=di;if(this._bytesLeft<=0)this._state=SEGMENT_START;break;// read segment and buffer it, process as exif
case SEGMENT_BUFFER:this._app1buffer=this._buffer(this._bytesLeft);this._app1pos=0;this._state=SEGMENT_BUFFER_DATA;break;// read segment and buffer it, process as exif
case SEGMENT_BUFFER_DATA:di=Math.min(this._bytesLeft,data.length-i);var buf_slice=slice(data,i,i+di);copy(buf_slice,this._app1buffer,this._app1pos);this._app1pos+=buf_slice.length;i+=di;this._bytesLeft-=di;if(this._bytesLeft<=0){var _buf=this._app1buffer;// eslint-disable-line
this._app1buffer=null;if(this._markerCode===0xE1/* APP1 */&&// compare with 'Exif\0\0'
_buf[0]===0x45&&_buf[1]===0x78&&_buf[2]===0x69&&_buf[3]===0x66&&_buf[4]===0x00&&_buf[5]===0x00){// EXIF
if(this._removeExif){_buf=null;}else{try{_buf=filterExif(_buf,{maxEntrySize:this._maxEntrySize,onIFDEntry:this._onIFDEntry});}catch(err){_buf=null;// unexpected errors inside EXIF parser
if(err.code&&err.code!=='EBADDATA'){this.onError(err);return;}}}}else{// not EXIF, maybe XMP
/* eslint-disable no-lonely-if */if(this._filter===true)_buf=null;}if(_buf){var buf2=this._buffer(4);buf2[0]=0xFF;buf2[1]=this._markerCode;buf2[2]=_buf.length+2>>>8&0xFF;buf2[3]=_buf.length+2&0xFF;this.onData(buf2);this.onData(_buf);}this._state=SEGMENT_START;}break;// read image until we get FF
case IMAGE:var start=i;while(i<data.length){if(data[i]===0xFF){if(i+1<data.length){b=data[i+1];// skip FF and restart markers
if(b===0x00||b>=0xD0&&b<0xD8){i+=2;continue;}}break;}i+=1;}if(!this._removeImage){this.onData(slice(data,start,i));}if(i<data.length){this._state=IMAGE_FF;i+=1;}break;// process possible segment inside image
case IMAGE_FF:// 00 - escaped FF, D0-D7 - restart markers, FF - just padding
if(b===0x00||b>=0xD0&&b<0xD8||b===0xFF){if(!this._removeImage){this.onData(this._buffer([255,b]));}this._state=b===0xFF?IMAGE_FF:IMAGE;i+=1;break;}this._state=SEGMENT_MARKER;break;// ignore the rest of the data
case FINAL:i+=1;break;}}this._bytesRead+=data.length;};JpegFilter.prototype.end=function(){switch(this._state){case FILE_START:case FILE_START_FF:case SEGMENT_IGNORE:case SEGMENT_PIPE:case SEGMENT_PIPE_DATA:case SEGMENT_BUFFER:case SEGMENT_BUFFER_DATA:// in those 6 states arbitrary data of a fixed length
// is expected, and we didn't get any
//
this._error("unexpected end of file (offset ".concat(toHex(this._bytesRead),")"),'EBADDATA');break;case FINAL:break;default:// otherwise just simulate EOI segment
//
this.push(this._buffer([0xFF,0xD9]));}};JpegFilter.prototype.onData=function(chunk){this.output.push(chunk);};JpegFilter.prototype.onEnd=function(){};JpegFilter.prototype.onError=function(err){throw err;};// Concatenate multiple Uint8Arrays
var arrayConcat=function arrayConcat(list){var size=0;var pos=0;for(var i=0;i<list.length;i+=1){size+=list[i].length;}var result=new Uint8Array(size);for(var _i2=0;_i2<list.length;_i2+=1){result.set(list[_i2],pos);pos+=list[_i2].length;}return result;};var getJpegHeader=function getJpegHeader(file){return new Promise(function(resolve,reject){if(!file){resolve();return;}var reader=new FileReader();reader.onloadend=function(){var fileData=new Uint8Array(reader.result);if(fileData[0]===0xFF&&fileData[1]===0xD8){// only keep comments and exif in header
var filter=JpegFilter({removeImage:true,filter:true,removeICC:true});try{filter.push(fileData);filter.end();}catch(err){reject(new Error('Bad image.'));return;}var tmp=arrayConcat(filter.output);// cut off last 2 bytes (EOI, 0xFFD9),
// they are always added by filter_jpeg on end
resolve(tmp.subarray(0,tmp.length-2));}resolve();};reader.readAsArrayBuffer(file);});};var pica$1=pica({tile:200});var _resizeImage=function resizeImage(file,config){return new Promise(function(resolve,reject){var slice=file.slice||file.webkitSlice||file.mozSlice;var ext=file.name.split('.').pop().toLowerCase();if(['bmp','jpg','jpeg','png'].indexOf(ext)===-1){resolve(file);// Skip resize
return;}var jpegHeader;var img=new Image();img.onload=function(){window.URL.revokeObjectURL(img.src);var quality=ext==='jpeg'||ext==='jpg'?3:undefined;var width=Math.min(img.height*config.width/config.height,img.width);var alpha=ext==='png'||file.type==='image/png';var source=document.createElement('canvas');var dest=document.createElement('canvas');var unsharpAmount=80;var unsharpRadius=0.6;var unsharpThreshold=2;source.width=width;source.height=img.height;dest.width=config.width;dest.height=config.height;source.getContext('2d').drawImage(img,0,0,width,img.height);pica$1.resize(source,dest,{alpha:alpha,unsharpAmount:unsharpAmount,unsharpRadius:unsharpRadius,unsharpThreshold:unsharpThreshold}).then(function(){return pica$1.toBlob(dest,file.type,quality);}).then(function(blob){var jpegBlob;var jpegBody;if(jpegHeader){// remove JPEG header (2 bytes) and JFIF segment (18 bytes),
// assuming JFIF is always present and always the same in all
// images from canvas
jpegBody=slice.call(blob,20);jpegBlob=new Blob([jpegHeader,jpegBody],{type:file.type});}var name=file.name;file=jpegBlob||blob;file.name=name;resolve(file);});};img.onerror=function(){window.URL.revokeObjectURL(img.src);reject(new Error('Bad image.'));};getJpegHeader(file).then(function(header){jpegHeader=header;img.src=window.URL.createObjectURL(file);});});};// Concatenate multiple Uint8Arrays
var arrayConcat$1=function arrayConcat$1(list){var size=0;var pos=0;for(var i=0;i<list.length;i+=1){size+=list[i].length;}var result=new Uint8Array(size);for(var _i3=0;_i3<list.length;_i3+=1){result.set(list[_i3],pos);pos+=list[_i3].length;}return result;};var cleanupJpegExif=function cleanupJpegExif(file){var keepOrientation=arguments.length>1&&arguments[1]!==undefined?arguments[1]:false;var keepICCandAPP=arguments.length>2&&arguments[2]!==undefined?arguments[2]:false;return FileUtils.blobToArrayBuffer(file).then(function(fileArray){var fileData=new Uint8Array(fileArray);var defaultOrientation=exif.getOrientation(fileArray);if(fileData[0]===0xFF&&fileData[1]===0xD8){// only keep comments and exif in header
var filter=JpegFilter({removeExif:true,removeComments:true,filter:true,removeICCandAPP:!keepICCandAPP});try{filter.push(fileData);filter.end();}catch(err){console.error(err);return file;}var tmp=arrayConcat$1(filter.output);// cut off last 2 bytes (EOI, 0xFFD9),
// they are always added by filter_jpeg on end
var newFileBuffArray=tmp.subarray(0,tmp.length-2);if(keepOrientation&&defaultOrientation){var newExif=exif.generateExifOrientation(defaultOrientation);newFileBuffArray=exif.overwriteInFile(newFileBuffArray.buffer,newExif);}return new Blob([newFileBuffArray]);}return file;});};// Generates guid-like random string
var guid=function guid(len){// eslint-disable-next-line
return new Array(len).join().replace(/(.|$)/g,function(){return(Math.random()*36|0).toString(36)[Math.random()<0.5?'toString':'toUpperCase']();});};/**
   * Return a normalized file size as number or null if conditions are not fullfilled
   * @param {object} file
   * @returns {number|null}
   */var normalizeFileSize=function normalizeFileSize(file){var normalizedFileSize=null;if(file.size){if(typeof file.size==='number'){normalizedFileSize=file.size;}else if(typeof file.size==='string'&&file.size===parseInt(file.size,10).toString()){// convert when it's "200", but not when it's "200x250" (eg. facebook, instagram)
normalizedFileSize=parseInt(file.size,10);}}return normalizedFileSize;};/**
   * Return a normalized file object from different sources
   * @param {object} file - file object to be normalized
   * @param {object} cloudParams
   * @param {object} cloudParams.currentCloud - settings of currently selected cloud provider
   * @param {object} cloudParams.cloudFolders - list of all folders of the selected cloud
   * @param {object} cloudParams.selectedCloudPath - path to the selected folder
   * @returns {object}
   */var normalizeFile=function normalizeFile(file,_ref3){var currentCloud=_ref3.currentCloud,cloudFolders=_ref3.cloudFolders,selectedCloudPath=_ref3.selectedCloudPath;if(file instanceof File||file instanceof Blob){file={source:'local_file_system',mimetype:file.type,name:file.name,path:file.path||file.name,size:file.size,originalFile:file};}if(file.source==='dragged-from-web'){file.name=file.url.split('/').pop();file.path=file.url;file.mimetype='text/html';var ext=file.url.split('.').pop();var allowed=['jpg','jpeg','png','tiff','gif','bmp'];if(ext&&allowed.indexOf(ext.toLowerCase())!==-1){file.thumbnail=file.url;file.mimetype="image/".concat(ext);}}// link_path exists on responses from cloud().metadata(...)
if(file.link_path){file.source='url';file.path=file.link_path;file.name=file.display_name;file.mimetype=file.type;}// Reconstruct "original path" for cloud files from current paths
// because file paths from the API are not human readable and do not
// represent the entire folder tree
if(file.sourceKind==='cloud'&&currentCloud&&currentCloud.path){var originalPath=currentCloud.path.map(function(p){return cloudFolders[p]&&cloudFolders[p].name;}).filter(function(p){return p;}).join('/');// Folder selection sets a "selected" path so we can determine the parent folder of the selected files
var folderName=cloudFolders[selectedCloudPath]&&cloudFolders[selectedCloudPath].name;if(folderName){originalPath=originalPath?"".concat(originalPath,"/").concat(folderName):folderName;}file.originalPath=originalPath?"/".concat(originalPath,"/").concat(file.name):"/".concat(file.name);}file.uuid=guid(16);file.uploadId=file.uuid;file.progress=0;file.progressSize='';file.size=normalizeFileSize(file);return file;};// All files in upload queue need to be simple objects with required "source" and "name" keys.
var log=logger.context('picker');var isNumber=function isNumber(n){return!isNaN(parseFloat(n))&&!isNaN(n-0);};var STATES={waiting:'waiting',uploading:'uploading',done:'done',failed:'failed',paused:'paused'};var uploadQueue=function uploadQueue(apiClient){var initialState=arguments.length>1&&arguments[1]!==undefined?arguments[1]:{};var addFile=function addFile(context,file){var maxFilesReached=function maxFilesReached(){if(!context.getters.maxFiles||(context.getters.fileCount||0)<context.getters.maxFiles){return false;}var filesText=context.getters.maxFiles===1?'file':'files';var errorMsg=errors(context.getters.lang,context.getters.customText).ERROR_MAX_FILES_REACHED.replace('{maxFiles}',context.getters.maxFiles).replace('{filesText}',filesText);context.dispatch('showNotification',errorMsg);return true;};/**
       * Check if file size is smaller than provided in maxSize option
       * @param {Object} normalizedFile
       */var fileIsSmallEnough=function fileIsSmallEnough(normalizedFile){if(context.getters.maxSize===undefined||!normalizedFile.size){return true;}if(normalizedFile.size<context.getters.maxSize){return true;}var errorMsg=errors(context.getters.lang,context.getters.customText).ERROR_FILE_TOO_BIG.replace('{displayName}',displayName(normalizedFile)).replace('{roundFileSize}',readableSize(context.getters.maxSize));context.dispatch('showNotification',errorMsg);return false;};var checkFileSize=function checkFileSize(rawFile,opts){if(!opts.shouldBlock){return Promise.resolve();}if(file.type.indexOf('image')!==0){return Promise.resolve();}return new Promise(function(resolve,reject){var fr=new FileReader();fr.onload=function(){var img=new Image();img.onload=function(){if(opts.imageMin&&opts.imageMin[0]>img.width){return reject(new Error("Incorrect image size. Minimum width is ".concat(opts.imageMin[0],"px but image have ").concat(img.width,"px")));}if(opts.imageMin&&opts.imageMin[1]>img.height){return reject(new Error("Incorrect image size. Minimum height is ".concat(opts.imageMin[1],"px but image have ").concat(img.height,"px")));}if(opts.imageMax&&opts.imageMax[0]<img.width){return reject(new Error("Incorrect image size. Maximum width is ".concat(opts.imageMax[0],"px but image have ").concat(img.width,"px")));}if(opts.imageMax&&opts.imageMax[1]<img.height){return reject(new Error("Incorrect image size. Maximum height is ".concat(opts.imageMax[1],"px but image have ").concat(img.height,"px")));}return resolve();};img.src=fr.result;};fr.readAsDataURL(rawFile);});};var fileIsAcceptable=function fileIsAcceptable(normalizedFile){if(canAcceptThisFile(normalizedFile,context.getters.accept)){return true;}var errorMsg=errors(context.getters.lang,context.getters.customText).ERROR_FILE_NOT_ACCEPTABLE.replace('{displayName}',displayName(normalizedFile)).replace('{types}',context.getters.accept);context.dispatch('showNotification',errorMsg);return false;};var fireOnFileSelected=function fireOnFileSelected(normalizedFile){if(!context.getters.onFileSelected){return Promise.resolve();}return new Promise(function(resolve,reject){try{var result=context.getters.onFileSelected(convertFileForOutsideWorld(normalizedFile,context.getters));if(result instanceof Promise){result.then(resolve)["catch"](reject);}else{resolve(result);}}catch(err){reject(err);}});};var startUploadImmediatelyMaybe=function startUploadImmediatelyMaybe(){if(context.getters.startUploadingWhenMaxFilesReached===true&&context.getters.onlyFilesWaiting.length===context.getters.maxFiles){context.dispatch('startUploading');}};return new Promise(function(resolve){// If file is already initialized we should remove it instead
// TODO: Rename this to something like toggleFile or split this out
if(file&&file.state!==undefined){context.dispatch('cancelUpload',file.uuid);context.commit('DESELECT_FILE',file.uuid);resolve();return;}// addCloudFolder dispatches addFile for every file in the folder
// This returns immediately because we don't mark folders for the queue
// TODO Consider an approach where folders are added (might be less complicated?)
if(file&&file.folder){context.dispatch('addCloudFolder',{name:file.source,path:file.path});resolve();return;}var normalizedFile=normalizeFile(file,context);fireOnFileSelected(normalizedFile).then(function(newFile){if(newFile){normalizedFile=_objectSpread({},normalizedFile,{name:newFile.name||newFile.filename||normalizedFile.name});}checkFileSize(file,{imageMax:context.getters.imageMax,imageMin:context.getters.imageMin,shouldBlock:context.getters.imageMinMaxBlock}).then(function(){if(maxFilesReached()){return resolve();}if(fileIsAcceptable(normalizedFile)&&fileIsSmallEnough(normalizedFile)){log('Selected file:',file);context.commit('INITIALIZE_FILE',normalizedFile);context.commit('MARK_FILE_AS_WAITING',normalizedFile.uuid);// If we reach maxFiles we might be starting all uploads
startUploadImmediatelyMaybe();// Start uploading this file immediately if background uploads are on
if(!context.getters.uploadStarted&&context.getters.uploadInBackground){context.dispatch('uploadMoreMaybe');}// Single image flow
if(_isImage(normalizedFile)&&isEditableImage(normalizedFile)&&context.getters.maxFiles===1&&!context.getters.disableTransformer&&!context.getters.uploadStarted){context.commit('CHANGE_ROUTE',['transform',normalizedFile.uuid]);}else if(context.getters.maxFiles===1){// Go to summary screen for a single selected file (non-image)
context.commit('CHANGE_ROUTE',['summary']);}else if(context.getters.maxFiles>1&&context.getters.fileCount===context.getters.maxFiles&&context.getters.route[0]!=='summary'){// Go to summary screen if maxFiles is reached
context.commit('CHANGE_ROUTE',['summary']);}else if(context.getters.maxFiles>1&&normalizedFile.source==='local_file_system'&&context.getters.route[0]!=='summary'){// Go to summary screen after selecting multiple local files
context.commit('CHANGE_ROUTE',['summary']);}}return resolve();})["catch"](function(err){if(file.uuid){context.dispatch('cancelUpload',file.uuid);context.commit('DESELECT_FILE',file.uuid);}context.dispatch('showNotification',err.message?err.message:err);return resolve();});})["catch"](function(err){if(file.uuid){context.dispatch('cancelUpload',file.uuid);context.commit('DESELECT_FILE',file.uuid);}context.dispatch('showNotification',err.message?err.message:err);resolve();});});};var startFakeProgress=function startFakeProgress(context,file){var clamp=function clamp(n,min,max){if(n<min)return min;if(n>max)return max;return n;};var inc=function inc(){var n=file.progress/100;var amount;if(n>1){return;}if(n>=0&&n<0.2)amount=0.1;else if(n>=0.2&&n<0.5)amount=0.04;else if(n>=0.5&&n<0.8)amount=0.02;else if(n>=0.8&&n<0.99)amount=0.005;else{amount=0;}n=clamp(n+amount,0,0.994);context.commit('SET_FILE_UPLOAD_PROGRESS',{uuid:file.uuid,progress:Math.round(n*100)});var progressEvent={totalBytes:Math.min(file.size,Math.round(file.size*Math.max(n,0.01))),totalPercent:Math.round(n*100)};if(context.getters.onFileUploadProgress){context.getters.onFileUploadProgress(convertFileForOutsideWorld(file,context.getters),progressEvent);}};var work=function work(){inc();setTimeout(function(){if(!file)return;if(file.state!==STATES.uploading){context.commit('SET_FILE_UPLOAD_PROGRESS',{uuid:file.uuid,progress:100});if(context.getters.onFileUploadProgress){context.getters.onFileUploadProgress(convertFileForOutsideWorld(file,context.getters),{totalBytes:file.size,totalPercent:100});}return;}work();},150);};work();};// A file can be manually passed in or pulled from state
var uploadOne=function uploadOne(context,manualFile){var pendingRequest;var file=manualFile||context.getters.onlyFilesWaiting[0];var uploadConfig=_objectSpread({progressInterval:10},context.getters.uploadConfig,{onRetry:function onRetry(payload){context.dispatch('checkNetworkXHR');if(context.getters.uploadConfig&&context.getters.uploadConfig.onRetry){context.getters.uploadConfig.onRetry(payload);}},onProgress:function onProgress(progressEvent){context.commit('SET_FILE_UPLOAD_PROGRESS',{uuid:file.uuid,progress:progressEvent.totalPercent,progressSize:readableSize(progressEvent.totalBytes)});if(context.getters.onFileUploadProgress){context.getters.onFileUploadProgress(convertFileForOutsideWorld(file,context.getters),progressEvent);}}});var token={};var storeTo=_objectSpread({},context.getters.storeTo);if(context.getters.disableStorageKey){if(context.getters.storeTo&&context.getters.storeTo.path){storeTo.path="".concat(context.getters.storeTo.path).concat(file.name);}else{storeTo.path=file.name;}}// TODO How to rename files in cloudrouter?
if(file.sourceKind!=='cloud'){storeTo.filename=file.name;}context.commit('MARK_FILE_AS_UPLOADING',{uuid:file.uuid,token:token});log('Upload started:',file);/**
       * Different sources are mapped to their own upload methods:
       * local and transformed files -> client.upload
       * cloud files -> client.cloud().store/link
       * any urls -> client.storeURL
       */if(file.transformed){// Start multi-part upload
pendingRequest=context.dispatch('resizeImageMaybe',file.transformed).then(function(blob){context.dispatch('runCallbackUploadStarted',file.uuid);return blob;}).then(function(blob){var removeExif=context.getters.removeExif;if(removeExif){return cleanupJpegExif(blob,removeExif.keepOrientation,removeExif.keepICCandAPP);}return blob;}).then(function(blob){// @todo fix tests mocks and uncomment this
// if (!blob) {
//   return Promise.reject(new Error('Missing file element for upload'));
// }
if(!context.getters.files[file.uuid]){return Promise.resolve();}// convert some strange blob to correct file object with name
if(blob&&blob.toString()==='[object Blob]'){// custom blob with name Oo
blob=new File([blob],blob.name);}return apiClient.upload(blob,uploadConfig,storeTo,token// Cancellation/pause/resume token
);});}else if(file.originalFile){// Start multi-part upload
pendingRequest=context.dispatch('resizeImageMaybe',file.originalFile).then(function(newBlob){// update image data if it was resized
context.commit('UPDATE_FILE_AFTER_RESIZE',{uuid:file.uuid,blob:newBlob});return newBlob;}).then(function(blob){context.dispatch('runCallbackUploadStarted',file.uuid);return blob;}).then(function(blob){var removeExif=context.getters.removeExif;if(removeExif){return cleanupJpegExif(blob,removeExif.keepOrientation,removeExif.keepICCandAPP);}return blob;}).then(function(blob){// @todo fix tests mocks and uncomment this
// if (!blob) {
//   return Promise.reject(new Error('Missing file element for upload'));
// }
// A file could be deselected after resize but before upload
if(!context.getters.files[file.uuid]){return Promise.resolve();}// convert some strange blob to correct file object with name
if(blob&&blob.toString()==='[object Blob]'){// custom blob with name Oo
blob=new File([blob],blob.name);}return apiClient.upload(blob,uploadConfig,storeTo,token// Cancellation/pause/resume token
);});}else if(file.source==='url'||file.source==='dragged-from-web'){context.dispatch('runCallbackUploadStarted',file.uuid);// Store custom URL
pendingRequest=apiClient.storeURL(file.path,storeTo,token);}else if(file.sourceKind==='cloud'){var customSource={};if(context.getters.customSourcePath){customSource.customSourcePath=context.getters.customSourcePath;}if(context.getters.customSourceContainer){customSource.customSourceContainer=context.getters.customSourceContainer;}context.dispatch('runCallbackUploadStarted',file.uuid);// Hit CloudRouter to link or store files
var cloudClient=context.getters.cloudClient;pendingRequest=cloudClient.store(file.source,file.path,storeTo,customSource,token);}// Fake file progress for non-local files (not background uploads)
if(!file.transformed&&file.source!=='local_file_system'&&context.getters.uploadStarted){startFakeProgress(context,file);}pendingRequest.then(function(uploadedFileMetadata){// Short circuit
if(!uploadedFileMetadata){context.commit('MARK_FILE_AS_DONE',{uuid:file.uuid});return undefined;}// Set fake progress to 100% on successful upload (not background uploads)
if(!file.transformed&&file.source!=='local_file_system'){context.commit('SET_FILE_UPLOAD_PROGRESS',{uuid:file.uuid,progress:100,progressSize:uploadedFileMetadata.size});}// Cloudrouter will not return HTTP errors. Errors are in 200 response body.
// This counts as a failed upload. Thrown error is caught in catch handler below.
// TODO Move this into api-client
if(uploadedFileMetadata.error&&uploadedFileMetadata.error.text){throw new Error(uploadedFileMetadata.error.text);}var uploadedFile=_objectSpread({},file,{},uploadedFileMetadata);// TODO Why is this being deleted?
delete uploadedFile.uuid;context.commit('MARK_FILE_AS_DONE',{uuid:file.uuid,uploadMetadata:uploadedFile});if(context.getters.onFileUploadFinished!==undefined){context.getters.onFileUploadFinished(convertFileForOutsideWorld(uploadedFile,context.getters));}log('Upload done:',file);return uploadedFileMetadata;})["catch"](function(error){if(!navigator.onLine){context.dispatch('onNetworkError',true);}// Set progress to 100 since it's actually "done"
context.commit('SET_FILE_UPLOAD_PROGRESS',{uuid:file.uuid,progress:100});context.commit('MARK_FILE_AS_FAILED',file.uuid);if(context.getters.onFileUploadFailed!==undefined){context.getters.onFileUploadFailed(convertFileForOutsideWorld(file,context.getters),error);}log('Upload failed:',file,error.message);});return pendingRequest;};// Actions
var finishUploadsMaybe=function finishUploadsMaybe(_ref4){var dispatch=_ref4.dispatch,_ref4$getters=_ref4.getters,allowManualRetry=_ref4$getters.allowManualRetry,filesDone=_ref4$getters.filesDone,filesList=_ref4$getters.filesList,filesFailed=_ref4$getters.filesFailed,uploadStarted=_ref4$getters.uploadStarted;if(uploadStarted&&filesList.length===filesDone.length+filesFailed.length){if(!(allowManualRetry&&filesFailed.length)){dispatch('allUploadsDone');}}};var uploadMoreMaybe=function uploadMoreMaybe(context){if(context.getters.filesUploading.length<context.getters.concurrency&&context.getters.onlyFilesWaiting.length>0){context.dispatch('uploadOne').then(function(){return context.dispatch('uploadMoreMaybe');})["catch"](function(){return context.dispatch('uploadMoreMaybe');});context.dispatch('uploadMoreMaybe');}else{context.dispatch('finishUploadsMaybe');}};var startUploading=function startUploading(context){// Already uploading - terminate this attempt.
if(context.getters.uploadStarted){return;}context.dispatch('checkNetworkNavigator');if(context.getters.onUploadStarted){context.getters.onUploadStarted(convertFileListForOutsideWorld(context.getters.filesList,context.getters));}context.commit('SET_UPLOAD_STARTED',true);context.commit('UPDATE_MOBILE_NAV_ACTIVE',false);// Go to summary screen directly once uploading starts
var baseRoute=context.getters.route[0];if(baseRoute!=='transform'&&baseRoute!=='summary'){context.commit('CHANGE_ROUTE',['summary']);}// Keep uploading until we have nothing left
context.dispatch('uploadMoreMaybe');};initialState=_objectSpread({files:{},uploadStarted:false},initialState);var mutations={CLEAR_FILES:function CLEAR_FILES(state){// because of reference we need to reset state of all files
Object.keys(state.files).forEach(function(uuid){var file=state.files[uuid];Vue.set(file,'state',undefined);Vue.set(file,'uuid',undefined);Vue["delete"](state.files,uuid);});},SET_UPLOAD_STARTED:function SET_UPLOAD_STARTED(state,value){state.uploadStarted=value;},INITIALIZE_FILE:function INITIALIZE_FILE(state,file){// Initial state
Vue.set(state.files,file.uuid,file);},MARK_FILE_AS_WAITING:function MARK_FILE_AS_WAITING(state,uuid){var file=state.files[uuid];Vue.set(file,'state',STATES.waiting);Vue.set(file,'progress',0);Vue.set(file,'progressSize',0);},DESELECT_FILE:function DESELECT_FILE(state,uuid){var file=state.files[uuid];Vue.set(file,'state',undefined);Vue.set(file,'uuid',undefined);Vue["delete"](state.files,uuid);},DESELECT_FOLDER:function DESELECT_FOLDER(state,folder){Object.keys(state.files).forEach(function(uuid){var file=state.files[uuid];if(isFileInFolder(file,folder)){Vue.set(file,'state',undefined);Vue.set(file,'uuid',undefined);Vue["delete"](state.files,uuid);}});},MARK_FILE_AS_UPLOADING:function MARK_FILE_AS_UPLOADING(state,_ref5){var uuid=_ref5.uuid,token=_ref5.token;var file=state.files[uuid];Vue.set(file,'state',STATES.uploading);if(token){file.token=token;}},MARK_FILE_AS_PAUSED:function MARK_FILE_AS_PAUSED(state,uuid){var file=state.files[uuid];Vue.set(file,'state',STATES.paused);},MARK_FILE_AS_DONE:function MARK_FILE_AS_DONE(state,_ref6){var uuid=_ref6.uuid,uploadMetadata=_ref6.uploadMetadata;var file=state.files[uuid];if(uploadMetadata){Object.keys(uploadMetadata).forEach(function(key){Vue.set(file,key,uploadMetadata[key]);});}Vue.set(file,'state',STATES.done);},MARK_FILE_AS_FAILED:function MARK_FILE_AS_FAILED(state,uuid){var file=state.files[uuid];if(file){Vue.set(file,'state',STATES.failed);}},SET_FILE_UPLOAD_PROGRESS:function SET_FILE_UPLOAD_PROGRESS(state,_ref7){var uuid=_ref7.uuid,progress=_ref7.progress,progressSize=_ref7.progressSize;var file=state.files[uuid];if(file){Vue["delete"](file,'progress');Vue["delete"](file,'progressSize');Vue.set(file,'progress',progress);Vue.set(file,'progressSize',progressSize);}},SET_FILE_CROP_DATA:function SET_FILE_CROP_DATA(state,_ref8){var uuid=_ref8.uuid,cropData=_ref8.cropData,imageData=_ref8.imageData;var file=state.files[uuid];Vue.set(file,'cropData',{originalImageSize:[imageData.naturalWidth,imageData.naturalHeight],cropArea:{position:[cropData.x,cropData.y],size:[cropData.width,cropData.height]}});},SET_FILE_ROTATION:function SET_FILE_ROTATION(state,_ref9){var uuid=_ref9.uuid,rotation=_ref9.rotation;var file=state.files[uuid];if(rotation===0){return false;}Vue.set(file,'rotated',{value:Math.abs(rotation),direction:rotation>0?'CW':'CCW'});return true;},SET_FILE_TRANSFORMATION:function SET_FILE_TRANSFORMATION(state,_ref10){var uuid=_ref10.uuid,blob=_ref10.blob,transform=_ref10.transform;var file=state.files[uuid];file.state=STATES.waiting;if(!file.originalSize){Vue.set(file,'originalSize',file.size);}if(!file.originalName){Vue.set(file,'originalName',file.name);}if(transform==='circle'||transform==='crop'){Vue.set(file,'cropped',true);}Vue.set(file,'transformed',blob);Vue.set(file,'size',blob.size);Vue.set(file,'name',blob.name);Vue.set(file,'progress',0);Vue.set(file,'progressSize','');},REMOVE_FILE_TRANSFORMATION:function REMOVE_FILE_TRANSFORMATION(state,uuid){var file=state.files[uuid];Vue["delete"](file,'transformed');Vue["delete"](file,'cropped');Vue["delete"](file,'cropData');Vue["delete"](file,'rotated');if(file.originalSize){Vue.set(file,'size',file.originalSize);Vue["delete"](file,'originalSize');}if(file.originalName){Vue.set(file,'name',file.originalName);Vue["delete"](file,'originalName');}},REMOVE_SOURCE_FROM_WAITING:function REMOVE_SOURCE_FROM_WAITING(state,source){Object.keys(state.files).forEach(function(uuid){var file=state.files[uuid];if(file.source===source){Vue.set(file,'state',undefined);Vue.set(file,'uuid',undefined);Vue["delete"](state.files,uuid);}});},REMOVE_CLOUDS_FROM_WAITING:function REMOVE_CLOUDS_FROM_WAITING(state){Object.keys(state.files).forEach(function(uuid){var file=state.files[uuid];if(file.sourceKind==='cloud'){Vue.set(file,'state',undefined);Vue.set(file,'uuid',undefined);Vue["delete"](state.files,uuid);}});},UPDATE_FILE_AFTER_RESIZE:function UPDATE_FILE_AFTER_RESIZE(state,_ref11){var uuid=_ref11.uuid,blob=_ref11.blob;var file=state.files[uuid];Vue.set(file,'size',blob.size);Vue.set(file,'progress',0);Vue.set(file,'progressSize','');}};var actions={addFile:addFile,finishUploadsMaybe:finishUploadsMaybe,startUploading:startUploading,uploadMoreMaybe:uploadMoreMaybe,uploadOne:uploadOne,runCallbackUploadStarted:function runCallbackUploadStarted(_ref12,uuid){var state=_ref12.state,getters=_ref12.getters;if(!getters.onFileUploadStarted){return;}var file=state.files[uuid];getters.onFileUploadStarted(convertFileForOutsideWorld(file,getters));},cancelUpload:function cancelUpload(_ref13,uuid){var state=_ref13.state;if(!uuid){return;}var file=state.files[uuid];if(file&&file.token&&file.token.cancel){file.token.cancel();}},cancelFolderUpload:function cancelFolderUpload(_ref14,folder){var dispatch=_ref14.dispatch,state=_ref14.state;lodash_values(state.files).filter(function(file){// Ignore non-cloud files
if(file.sourceKind!=='cloud'){return true;}// This matches files under a path recursively, because of indexOf
return file.path.indexOf(folder.path)>=0;}).map(function(file){return file.uuid;}).forEach(function(uuid){dispatch('cancelUpload',uuid);});},cancelAllUploads:function cancelAllUploads(_ref15){var dispatch=_ref15.dispatch,state=_ref15.state;var uuids=Object.keys(state.files);uuids.forEach(function(uuid){dispatch('cancelUpload',uuid);});},deselectAllFiles:function deselectAllFiles(context){context.dispatch('cancelAllUploads');context.getters.filesList.forEach(function(file){if(file&&file.uuid){context.commit('DESELECT_FILE',file.uuid);}});},deselectFolder:function deselectFolder(context,folder){context.dispatch('cancelFolderUpload',folder);context.commit('DESELECT_FOLDER',folder);},resizeImageMaybe:function resizeImageMaybe(_ref16,blob){var dispatch=_ref16.dispatch,_ref16$getters=_ref16.getters,imageDim=_ref16$getters.imageDim,imageMin=_ref16$getters.imageMin,imageMax=_ref16$getters.imageMax,imageMinMaxBlock=_ref16$getters.imageMinMaxBlock;// Skip files that aren't images or if we have no resize options
if(!blob||!isEditableImage(blob)||!(imageDim||imageMin||imageMax)){return Promise.resolve(blob);}return new Promise(function(resolve){var img=new Image();var url=window.URL.createObjectURL(blob);img.src=url;img.onload=function(){window.URL.revokeObjectURL(url);var config={width:img.width,height:img.height};var ratio=config.width/config.height;if(imageMinMaxBlock){return Promise.resolve(blob);}if(imageDim){if(imageDim[0]){config.width=imageDim[0];config.height=config.width/ratio;}else if(imageDim[1]){config.height=imageDim[1];config.width=config.height*ratio;}}else{if(imageMin){if(config.width<imageMin[0]){config.width=imageMin[0];config.height=config.width/ratio;}if(config.height<imageMin[1]){config.height=imageMin[1];config.width=config.height*ratio;}}if(imageMax){if(config.width>imageMax[0]){config.width=imageMax[0];config.height=config.width/ratio;}if(config.height>imageMax[1]){config.height=imageMax[1];config.width=config.height*ratio;}}}config.width=parseInt(config.width,10);config.height=parseInt(config.height,10);// Resize with Pica -- maintain width/height ratio
if(img.width!==config.width&&img.height!==config.height){return dispatch('resizeImage',{blob:blob,config:config}).then(function(newBlob){return resolve(newBlob);})["catch"](function(){return resolve(blob);});}return resolve(blob);};});},resizeImage:function resizeImage(context,_ref17){var blob=_ref17.blob,config=_ref17.config;return _resizeImage(blob,config);},setFileCropData:function setFileCropData(_ref18,_ref19){var getters=_ref18.getters,commit=_ref18.commit;var uuid=_ref19.uuid,cropData=_ref19.cropData,imageData=_ref19.imageData,rotation=_ref19.rotation;var file=getters.files[uuid];function isCropped(){if(isNumber(cropData.x)&&isNumber(cropData.y)!==undefined&&cropData.width&&cropData.height){if(rotation===180){return imageData.naturalWidth!==cropData.width||imageData.naturalHeight!==cropData.height;}return imageData.naturalHeight!==cropData.width||imageData.naturalWidth!==cropData.height;}return false;}commit('SET_FILE_ROTATION',{uuid:uuid,rotation:rotation});if(!isCropped()){return false;}commit('SET_FILE_CROP_DATA',{uuid:uuid,cropData:cropData,imageData:imageData});if(typeof getters.onFileCropped==='function'){getters.onFileCropped(convertFileForOutsideWorld(file,getters));}return false;},transformImage:function transformImage(context,uuid){return context.commit('CHANGE_ROUTE',['transform',uuid]);},pauseAllUploads:function pauseAllUploads(_ref20){var commit=_ref20.commit,filesUploading=_ref20.getters.filesUploading;if(filesUploading.length){filesUploading.forEach(function(file){if(file.token&&file.token.pause){file.token.pause();commit('MARK_FILE_AS_PAUSED',file.uuid);}});}},retryAllFailedFiles:function retryAllFailedFiles(_ref21){var commit=_ref21.commit,dispatch=_ref21.dispatch,_ref21$getters=_ref21.getters,filesFailed=_ref21$getters.filesFailed,filesPaused=_ref21$getters.filesPaused;if(filesPaused.length){filesPaused.forEach(function(file){if(file.token&&file.token.resume){file.token.resume();commit('MARK_FILE_AS_UPLOADING',{uuid:file.uuid});}});}if(filesFailed.length){filesFailed.forEach(function(file){commit('MARK_FILE_AS_WAITING',file.uuid);dispatch('uploadMoreMaybe');});}}};var getters={files:function files(st){return st.files;},filesList:function filesList(st){return lodash_values(st.files);},filesUploading:function filesUploading(st,_ref22){var filesList=_ref22.filesList;return filesList.filter(function(f){return f.state===STATES.uploading;});},filesDone:function filesDone(st,_ref23){var filesList=_ref23.filesList;return filesList.filter(function(f){return f.state===STATES.done;});},filesFailed:function filesFailed(st,_ref24){var filesList=_ref24.filesList;return filesList.filter(function(f){return f.state===STATES.failed;});},filesPaused:function filesPaused(st,_ref25){var filesList=_ref25.filesList;return filesList.filter(function(f){return f.state===STATES.paused;});},fileCount:function fileCount(st,_ref26){var filesList=_ref26.filesList;return filesList.length;},onlyFilesWaiting:function onlyFilesWaiting(state,_ref27){var filesList=_ref27.filesList;return filesList.filter(function(f){return f.state===STATES.waiting;});},uploadStarted:function uploadStarted(st){return st.uploadStarted;},filesWaiting:function filesWaiting(state,_ref28){var filesList=_ref28.filesList;if(state.uploadStarted){return filesList.filter(function(f){return f.state===STATES.waiting;});}return filesList;},filesNeededCount:function filesNeededCount(state,_ref29){var minFiles=_ref29.minFiles,filesWaiting=_ref29.filesWaiting;return minFiles-filesWaiting.length;},canStartUpload:function canStartUpload(state,_ref30){var filesWaiting=_ref30.filesWaiting,minFiles=_ref30.minFiles;return filesWaiting.length>=minFiles;},canAddMoreFiles:function canAddMoreFiles(state,_ref31){var filesWaiting=_ref31.filesWaiting,maxFiles=_ref31.maxFiles;return filesWaiting.length<maxFiles;}};return{state:initialState,mutations:mutations,actions:actions,getters:getters};};//
var log$1=logger.context('picker');var script$p={components:{ContentHeader:ContentHeader,FooterNav:FooterNav,Loading:Loading,Modal:Modal,ProgressBar:ProgressBar},beforeDestroy:function beforeDestroy(){if(this.cropper){this.cropper.destroy();this.cropper=null;}},mounted:function mounted(){var _this22=this;log$1('Transform component mounted');this.$nextTick(function(){_this22.initialize();});},computed:_objectSpread({},index_esm.mapGetters(['getModuleUrl','apiClient','cropAspectRatio','cropFiles','cropForce','files','maxFiles','route','transformations','uploadStarted','filesList']),{filesNotCropped:function filesNotCropped(){return this.filesList.filter(function(file){return isEditableImage(file)&&!file.cropped;});},options:function options(){var opts=[];if(this.transformations.crop){opts.push('crop');}if(this.transformations.mask){opts.push('mask');}if(this.transformations.circle){opts.push('circle');if(typeof this.cropAspectRatio==='number'&&!isNaN(this.cropAspectRatio)&&this.cropAspectRatio!==1){opts.pop();}}if(this.transformations.rotate){opts.push('rotate');}return opts;},shouldGoBack:function shouldGoBack(){if(this.uploadStarted){return false;}if(this.cropFiles){return this.maxFiles>1;}return true;}}),data:function data(){return{cachedURL:null,cropper:null,ctx:null,fabric:null,file:{},hasCircle:false,maskContainer:null,oImg:null,pendingApply:false,rotation:0,state:'loading'};},methods:_objectSpread({},index_esm.mapActions(['deselectAllFiles','resizeImageMaybe','startUploading','uploadOne']),{apply:function apply(done){var _this23=this;var type=this.file.mimetype;var name=this.file.name;this.pendingApply=false;return new Promise(function(resolve){var canvas=_this23.cropper.getCroppedCanvas();if(_this23.state==='circle'){canvas=_this23.getRoundedCanvas(canvas);_this23.hasCircle=true;}if(_this23.state==='mask'){canvas=_this23.getMaskedCanvas(canvas);_this23.hasCircle=true;}if(_this23.hasCircle||isSVG$1(_this23.file)){var ext=_this23.file.name&&_this23.file.name.split('.').pop().toLowerCase();type='image/png';name=ext==='png'?name:"".concat(name,".png");}var transform=_this23.state;_this23.state='loading';canvas.toBlob(function(blob){// canvas object doesnt have any exif data, should we remove method or restore exif from original file?
_this23.resetEXIFOrientation(blob).then(function(finalBlob){if(_this23.cropper){finalBlob.name=name;_this23.$store.dispatch('setFileCropData',{uuid:_this23.file.uuid,cropData:_this23.cropper.getData(true),imageData:_this23.cropper.getImageData(),rotation:_this23.rotation});_this23.$store.commit('SET_FILE_TRANSFORMATION',{uuid:_this23.file.uuid,blob:finalBlob,transform:transform});if(!done){_this23.cropper.replace(window.URL.createObjectURL(finalBlob));}}resolve();});},type);});},capitalize:function capitalize(s){return s&&s[0].toUpperCase()+s.slice(1);},getMaskedCanvas:function getMaskedCanvas(src){var canvas=document.createElement('canvas');var context=canvas.getContext('2d');canvas.width=src.width;canvas.height=src.height;context.imageSmoothingEnabled=true;context.drawImage(this.svgMask,0,0,canvas.width,canvas.height);context.globalCompositeOperation='source-out';context.drawImage(src,0,0,canvas.width,canvas.height);return canvas;},getRoundedCanvas:function getRoundedCanvas(src){var canvas=document.createElement('canvas');var context=canvas.getContext('2d');var width=src.width;var height=src.height;canvas.width=width;canvas.height=height;context.imageSmoothingEnabled=true;context.drawImage(src,0,0,width,height);context.globalCompositeOperation='destination-in';context.beginPath();context.arc(width/2,height/2,Math.min(width,height)/2,0,2*Math.PI,true);context.fill();return canvas;},getImageURL:function getImageURL(file){var _this24=this;return new Promise(function(resolve){if(_this24.cachedURL){return resolve(_this24.cachedURL);}// Local images get resized first
if(file.originalFile){return _this24.resizeImageMaybe(file.originalFile).then(function(blob){_this24.cachedURL=window.URL.createObjectURL(blob);resolve(_this24.cachedURL);});}// If file is background uploading wait until it is done
if(file.state===STATES.uploading){var check=function check(){if(file.state===STATES.uploading){setTimeout(check,100);}else{_this24.cachedURL=_this24.signUrl(file.url);resolve(_this24.cachedURL);}};return check();}// Non-local files will be uploaded to obtain a Filestack CDN link
return _this24.uploadOne(file).then(function(data){_this24.cachedURL=_this24.signUrl(data.url);resolve(_this24.cachedURL);})["catch"](function(){_this24.state='errored';resolve();});});},genIconClass:function genIconClass(option){if(this.state===option&&!this.uploadStarted&&this.state!=='loading'&&this.state!=='errored'){return"fst-icon--".concat(option,"-blue");}return"fst-icon--".concat(option,"-black");},getSidebarClasses:function getSidebarClasses(option){return{'fst-sidebar__option--active':option===this.state,'fst-sidebar__option--disabled':this.state==='loading'||this.state==='errored'||this.uploadStarted};},goBack:function goBack(){if(this.maxFiles===1){this.$store.commit('REMOVE_FILE_TRANSFORMATION',this.file.uuid);this.$store.dispatch('deselectAllFiles');}this.$store.commit('GO_BACK_WITH_ROUTE');},handleApply:function handleApply(){if(this.state==='crop'||this.state==='circle'){var box=this.cropper.getCropBoxData();if(!box.width||!box.height){return;}}if(this.state!=='ready'&&this.state!=='loading'&&this.state!=='errored'){this.apply();}},handleReset:function handleReset(){if(this.state!=='loading'){this.$store.commit('REMOVE_FILE_TRANSFORMATION',this.file.uuid);this.state='loading';this.hasCircle=false;this.rotation=0;this.cropper.destroy();this.$refs.image.src='';this.initialize();}},handleClick:function handleClick(transform){this.state=transform;},handleNext:function handleNext(){if(this.state!=='loading'){var file=this.filesNotCropped[0];this.cachedURL=null;this.state='loading';this.hasCircle=false;this.rotation=0;this.$refs.image.src='';this.cropper.destroy();this.initialize(file.uuid);}},handleDone:function handleDone(){var _this25=this;if(this.state!=='ready'&&this.state!=='loading'&&this.state!=='errored'){this.apply(true).then(function(){console.log('crop done!!!!');_this25.goBack();});}else if(this.state!=='loading'){this.goBack();}},handleUpload:function handleUpload(){var _this26=this;if(this.state!=='ready'&&this.state!=='loading'&&this.state!=='errored'){this.apply(true).then(function(){return _this26.startUploading();});}else if(this.state!=='loading'){this.startUploading();}},initialize:function initialize(uuid){var _this27=this;var opts={aspectRatio:this.cropAspectRatio,autoCrop:false,autoCropArea:1,background:false,center:false,dragMode:'none',guides:false,toggleDragModeOnDblclick:false,viewMode:1,zoomable:true};uuid=uuid||this.route[1];this.file=this.files[uuid];loadModule(this.getModuleUrl('fs-cropper'),'fs-cropper').then(function(Cropper){_this27.getImageURL(_this27.file).then(function(url){log$1("Image transform URL ".concat(url));var img=_this27.$refs.image;if(img){img.src=url;img.addEventListener('ready',function(){log$1('Image for transformation loaded');_this27.state='ready';});img.addEventListener('error',function(e){log$1("Cannot load image to crop ".concat(e," imgTag"));_this27.state='errored';});// Initialize cropperjs on img
_this27.cropper=new Cropper(img,opts);}})["catch"](function(e){log$1("Cannot load image to crop ".concat(e));_this27.state='errored';});})["catch"](function(e){log$1("Cannot load cropper module ".concat(e));_this27.state='errored';});},resetEXIFOrientation:function resetEXIFOrientation(blob){if(blob.type!=='image/jpeg'){return Promise.resolve(blob);}// canvas doesnt contain exif data so reset orientation to 1
return FileUtils.blobToArrayBuffer(blob).then(function(buff){var exifData=exif.generateExifOrientation(1);var newBuf=exif.overwriteInFile(buff,exifData);return new Blob([newBuf],{type:blob.type});});},rotate:function rotate(deg){this.rotation+=deg;// Prevent very large degree values when rotating multiple times over.
if(this.rotation===270){this.rotation=-90;}else if(this.rotation===-180){this.rotation=180;}this.cropper.rotate(deg);},showSVGMask:function showSVGMask(fabric){var _this28=this;fabric.loadSVGFromURL(this.transformations.mask.url,function(objects){var path=objects[0];if(!path||!path.d){return;}var canvas=document.createElement('canvas');var width=Math.ceil(path.width);var height=Math.ceil(path.height);var f=new fabric.Canvas(canvas);f.setDimensions({width:width,height:height});var clipPath=new fabric.Path(path.d,{width:width,height:height,top:0,left:0,fill:'#000000',globalCompositeOperation:'destination-out'});var rect=new fabric.Rect({width:width+100,height:height+100,left:0,top:0,fill:_this28.transformations.mask.color,stroke:_this28.transformations.mask.color,strokeWidth:10});// Add SVG path to canvas, render it for cropperjs cropper-face overlay
f.add(rect);f.add(clipPath);f.renderAll();// Add fill to SVG path, render it to an image with fill for canvas image compositing
var overlay=f.toDataURL();var img=new Image();img.src=overlay;_this28.svgMask=img;img.onerror=function(){_this28.state='errored';};img.onload=function(){// Enable cropper and add rendered overlay as background-image on cropper-face
var box=document.querySelector('.fsp-picker .cropper-face');_this28.cropper.setAspectRatio(width/height);_this28.cropper.crop();box.style.background="url(".concat(overlay,") no-repeat");box.style.backgroundSize='100%';box.style.opacity='0.5';};},null,{crossOrigin:'anonymous'});},signUrl:function signUrl(url){var _this$apiClient$sessi=this.apiClient.session,policy=_this$apiClient$sessi.policy,signature=_this$apiClient$sessi.signature;if(policy&&signature){return"".concat(url,"?policy=").concat(policy,"&signature=").concat(signature);}return url;}}),watch:{state:function state(val){var _this29=this;var cropView=document.querySelector('.fsp-picker .cropper-view-box');var cropFace=document.querySelector('.fsp-picker .cropper-face');switch(val){case'ready':this.cropper.reset();this.cropper.clear();if(this.cropForce&&!this.file.cropped&&!!this.transformations.crop){this.state='crop';}if(this.cropForce&&!this.file.cropped&&!this.transformations.crop){this.state='circle';}if(!this.file){this.state='errored';break;}if(!this.file.transformed&&this.options.length===1&&this.options[0]==='mask'){this.state='mask';}break;case'circle':cropView.style.borderRadius='50%';cropFace.style.borderRadius='50%';cropFace.style.background='none';this.cropper.setAspectRatio(1);this.cropper.crop();break;case'crop':cropView.style.borderRadius='0px';cropFace.style.borderRadius='0px';cropFace.style.background='none';this.cropper.setAspectRatio(this.cropAspectRatio);this.cropper.crop();break;case'rotate':this.cropper.reset();this.cropper.clear();break;case'mask':{cropView.style.borderRadius='0px';cropFace.style.borderRadius='0px';loadModule(this.getModuleUrl('fs-fabric'),'fs-fabric').then(function(fabric){_this29.showSVGMask(fabric);});break;}}}}};/* script */var __vue_script__$p=script$p;/* template */var __vue_render__$p=function __vue_render__$p(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("modal",[_c("div",{attrs:{slot:"header"},slot:"header"},[_vm.shouldGoBack?_c("div",{staticClass:"fsp-transformer__go-back",attrs:{title:_vm.t("Go back")},on:{click:_vm.goBack}}):_vm._e(),_vm._v(" "),_c("content-header",{attrs:{"hide-menu":true}},[_c("span",{staticClass:"fsp-header-text--visible"},[_vm._v(_vm._s(_vm.t("Edit Image")))])])],1),_vm._v(" "),_c("div",{staticClass:"fst-sidebar",attrs:{slot:"sidebar"},slot:"sidebar"},_vm._l(_vm.options,function(option){return _c("div",{key:option,staticClass:"fst-sidebar__option","class":_vm.getSidebarClasses(option),attrs:{title:_vm.t(_vm.capitalize(option)),tabindex:"0"},on:{click:function click($event){return _vm.handleClick(option);},keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.handleClick(option);}}},[_c("span",{staticClass:"fst-icon","class":_vm.genIconClass(option)}),_vm._v("\n      "+_vm._s(_vm.t(_vm.capitalize(option)))+"\n    ")]);}),0),_vm._v(" "),_c("div",{staticClass:"fsp-transformer",attrs:{slot:"body"},slot:"body"},[_vm.state==="loading"?_c("loading"):_vm._e(),_vm._v(" "),_vm.state==="errored"?_c("div",{staticClass:"fsp-transformer__error"},[_c("div",{staticClass:"fst-icon--broken-image"}),_vm._v("\n      "+_vm._s(_vm.t("This image cannot be edited"))+"\n    ")]):_vm._e(),_vm._v(" "),_c("img",{directives:[{name:"show",rawName:"v-show",value:_vm.state!=="loading"&&_vm.state!=="errored",expression:"state !== 'loading' && state !== 'errored'"}],ref:"image",staticStyle:{"max-width":"100%"}}),_vm._v(" "),_c("div",{directives:[{name:"show",rawName:"v-show",value:_vm.state==="rotate",expression:"state === 'rotate'"}],staticClass:"fsp-transformer__rotate"},[_c("div",{staticClass:"fsp-transformer__rotate-left",attrs:{title:"Rotate -90°"},on:{click:function click($event){return _vm.rotate(-90);}}}),_vm._v(" "),_c("div",{staticClass:"fsp-transformer__rotate-right",attrs:{title:"Rotate 90°"},on:{click:function click($event){return _vm.rotate(90);}}})]),_vm._v(" "),_c("footer-nav",{attrs:{slot:"footer","is-visible":!_vm.uploadStarted},slot:"footer"},[_c("span",{staticClass:"fsp-button fsp-button--cancel","class":{"fsp-button--cancel-disabled":!_vm.file.transformed||_vm.state==="loading"},attrs:{slot:"nav-left"},on:{click:_vm.handleReset},slot:"nav-left"},[_vm._v("\n        "+_vm._s(_vm.t("Reset"))+"\n      ")]),_vm._v(" "),_c("div",{attrs:{slot:"nav-right"},slot:"nav-right"},[_vm.state!=="ready"&&_vm.state!=="loading"&&_vm.state!=="errored"?_c("span",{staticClass:"fsp-button fsp-button--outline",attrs:{title:"Save",tabindex:"0"},on:{click:_vm.handleApply,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.handleApply($event);}}},[_vm._v("\n          "+_vm._s(_vm.t("Save"))+"\n        ")]):_vm.maxFiles===1?_c("span",{staticClass:"fsp-button fsp-button--primary","class":{"fsp-button--disabled":_vm.state==="loading"},attrs:{title:_vm.t("Upload"),tabindex:"0"},on:{click:_vm.handleUpload,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.handleUpload($event);}}},[_vm._v("\n          "+_vm._s(_vm.t("Upload"))+"\n        ")]):_vm.cropForce&&_vm.filesNotCropped.length&&_vm.state!=="loading"?_c("span",{staticClass:"fsp-button fsp-button--primary",attrs:{title:"Next",tabindex:"0"},on:{click:_vm.handleNext,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.handleNext($event);}}},[_vm._v("\n          "+_vm._s(_vm.t("Next"))+"\n        ")]):_c("span",{staticClass:"fsp-button fsp-button--primary","class":{"fsp-button--disabled":_vm.state==="loading"},attrs:{title:"Done",tabindex:"0"},on:{click:_vm.handleDone,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.handleDone($event);}}},[_vm._v("\n          "+_vm._s(_vm.t("Done"))+"\n        ")])])]),_vm._v(" "),_c("footer-nav",{attrs:{slot:"footer","is-visible":_vm.uploadStarted,"full-width":true},slot:"footer"},[_c("span",{attrs:{slot:"nav-center"},slot:"nav-center"},[_c("progress-bar",{attrs:{progress:_vm.file.progress}})],1)])],1)]);};var __vue_staticRenderFns__$p=[];__vue_render__$p._withStripped=true;/* style */var __vue_inject_styles__$p=undefined;/* scoped */var __vue_scope_id__$p=undefined;/* module identifier */var __vue_module_identifier__$p=undefined;/* functional template */var __vue_is_functional_template__$p=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var Transform=normalizeComponent({render:__vue_render__$p,staticRenderFns:__vue_staticRenderFns__$p},__vue_inject_styles__$p,__vue_script__$p,__vue_scope_id__$p,__vue_is_functional_template__$p,__vue_module_identifier__$p,false,undefined,undefined,undefined);var propMap={'r':'rotate','ry':'rotateY','t':'translateX','ty':'translateY'};var transformsMap={'2':{'ry':180},'3':{'r':180},'4':{'r':180,'ry':180},'5':{'r':270,'ry':180},'6':{'ty':-1,'r':90},'7':{'ty':-1,'t':-1,'r':90,'ry':180},'8':{'t':-1,'r':270}};var transformOriginMap={'5':'top left','6':'bottom left','7':'bottom right','8':'top right'};function expandTransforms(transforms){var o={};var expanded=false;for(var prop in transforms){if(!expanded)expanded=true;var ep=propMap[prop];o[ep]=transforms[prop];}return expanded?o:null;}function getValue$1(prop,value){if(prop==='r'||prop==='ry'){return"".concat(value,"deg");}if(prop==='t'||prop==='ty'){return"".concat(value*100,"%");}}function expandTransform(transforms){var a=[];for(var prop in transforms){var ep=propMap[prop];a.push(ep+'('+getValue$1(prop,transforms[prop])+')');}return a.length?a.join(' '):null;}function expandTransformStrings(transforms){var o={};var expanded=false;for(var prop in transforms){if(!expanded)expanded=true;var ep=propMap[prop];o[ep]=ep+'('+getValue$1(prop,transforms[prop])+')';}return expanded?o:null;}/**
   * Takes the input EXIF orientation and returns the CSS rules needed to display the image correctly in the browser.
   * @param {(number|string)} orientation The EXIF orientation.
   * @returns {Exif2CssReturn} An object with `transform`, `transform-origin` (not shown in JSDoc because of hyphen), `transforms` and `transformStrings` properties.
   */function exif2css(orientation){var s="".concat(orientation);var transforms=transformsMap[s];var transform=expandTransform(transforms);var transformOrigin=transformOriginMap[s];var allTransforms=expandTransforms(transforms);var allTransformStrings=expandTransformStrings(transforms);var css={};if(transform){css['transform']=transform;}if(transformOrigin){css['transform-origin']=transformOrigin;}if(allTransforms){css['transforms']=allTransforms;}if(allTransformStrings){css['transformStrings']=allTransformStrings;}return css;}var roundFileSize=function roundFileSize(numb){if(numb>=1048576){return"".concat(Math.round(numb/1048576),"MB");}if(numb>=1024){return"".concat(Math.round(numb/1024),"KB");}return"".concat(numb,"B");};//
var script$q={props:{file:Object},computed:_objectSpread({},index_esm.mapGetters(['blobURLs','cropForce','disableTransformer','disableThumbnails','uploadStarted'])),methods:_objectSpread({},index_esm.mapActions(['addFile','transformImage']),{isEditable:function isEditable(file){return isEditableImage(file);},isDone:function isDone(file){return file.state===STATES.done;},isFailed:function isFailed(file){return file.state===STATES.failed;},isTransformed:function isTransformed(file){return file.transformed;},generateClass:function generateClass(file){if(isEditableImage(file)){return'fsp-grid__icon-image';}if(_isAudio(file)){return'fsp-grid__icon-audio';}if(file.mimetype==='application/pdf'){return'fsp-grid__icon-pdf';}return'fsp-grid__icon-file';},generateThumbnail:function generateThumbnail(file){if(this.blobURLs[file.uuid]){return this.blobURLs[file.uuid];}var imageBlob=file.transformed||file.originalFile;var url=window.URL.createObjectURL(imageBlob);this.$store.commit('SET_BLOB_URL',{uuid:file.uuid,url:url});// rotate according to exif
this.fixTmpThumbnail(file);return url;},fixTmpThumbnail:function fixTmpThumbnail(file){var _this30=this;FileUtils.blobToArrayBuffer(file.transformed||file.originalFile).then(function(fileArray){var cssOrientation=exif2css(exif.getOrientation(fileArray));var ref=_this30.$refs["thumb-".concat(file.uuid)];if(!ref||!cssOrientation||!cssOrientation.transform){return;}ref.style.transform=cssOrientation.transform;if(!cssOrientation['transform-origin']){return;}ref.style['transform-origin']=cssOrientation['transform-origin'];});},revert:function revert(file){this.$store.commit('REMOVE_FILE_TRANSFORMATION',file.uuid);this.$store.commit('REMOVE_BLOB_URL',file.uuid);},transform:function transform(uuid){this.$store.commit('REMOVE_BLOB_URL',uuid);this.transformImage(uuid);},translatedFileSize:function translatedFileSize(file){if(file.sourceKind==='cloud'&&!file.transformed){return'';}return roundFileSize(file.size);}})};/* script */var __vue_script__$q=script$q;/* template */var __vue_render__$q=function __vue_render__$q(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{staticClass:"fsp-summary__item",style:{opacity:_vm.uploadStarted&&_vm.isDone(_vm.file)?"0.7":"1"}},[(_vm.file.source==="local_file_system"||_vm.isTransformed(_vm.file))&&!_vm.disableThumbnails&&_vm.isEditable(_vm.file)?_c("img",{key:_vm.file.uuid,ref:"thumb-"+_vm.file.uuid,staticClass:"fsp-summary__thumbnail",attrs:{src:_vm.generateThumbnail(_vm.file)}}):_vm.isEditable(_vm.file)&&_vm.file.source!=="local_file_system"&&!_vm.disableThumbnails?_c("div",[_c("img",{staticClass:"fsp-summary__thumbnail",attrs:{src:_vm.file.thumbnail}})]):_c("div",{staticClass:"fsp-summary__thumbnail-container"},[_c("span",{"class":_vm.generateClass(_vm.file)})]),_vm._v(" "),_c("span",{staticClass:"fsp-summary__item-name",attrs:{title:_vm.file.name}},[_c("span",[_vm._v(_vm._s(_vm.file.name))]),_vm._v(" "),_c("span",{staticClass:"fsp-summary__size"},[!_vm.isFailed(_vm.file)&&(_vm.file.source==="local_file_system"||_vm.isTransformed(_vm.file))&&_vm.uploadStarted?_c("span",{staticClass:"fsp-summary__size-progress"},[_vm._v("\n        "+_vm._s(_vm.file.progressSize)+" /\n      ")]):_vm._e(),_vm._v(" "),_c("span",[_vm._v(" "+_vm._s(_vm.translatedFileSize(_vm.file)))])])]),_vm._v(" "),_vm.uploadStarted&&!_vm.isFailed(_vm.file)?_c("div",{staticClass:"fsp-summary__item-progress",style:{width:_vm.file.progress+"%"}}):_vm._e(),_vm._v(" "),_c("div",{staticClass:"fsp-summary__actions-container"},[!_vm.uploadStarted&&!_vm.disableTransformer&&!_vm.isTransformed(_vm.file)&&_vm.isEditable(_vm.file)?_c("span",{staticClass:"fsp-summary__action fsp-summary__action--button","class":{"fsp-summary__action--button-blue":_vm.cropForce},attrs:{tabindex:"0"},on:{click:function click($event){return _vm.transform(_vm.file.uuid);},keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.transform(_vm.file.uuid);}}},[_vm._v("\n      "+_vm._s(_vm.cropForce?_vm.t("Crop"):_vm.t("Edit"))+"\n    ")]):!_vm.uploadStarted&&_vm.isTransformed(_vm.file)?_c("span",{staticClass:"fsp-summary__action fsp-summary__action--button",on:{click:function click($event){return _vm.revert(_vm.file);}}},[_vm._v("\n      "+_vm._s(_vm.t("Revert"))+"\n    ")]):_vm._e(),_vm._v(" "),!_vm.uploadStarted&&_vm.isEditable(_vm.file)?_c("span",{staticClass:"fsp-summary__action-separator"}):_vm._e(),_vm._v(" "),!_vm.uploadStarted||!_vm.isDone(_vm.file)?_c("span",{staticClass:"fsp-summary__action fsp-summary__action--remove",attrs:{tabindex:"0"},on:{click:function click($event){return _vm.addFile(_vm.file);}}}):_vm._e()])]);};var __vue_staticRenderFns__$q=[];__vue_render__$q._withStripped=true;/* style */var __vue_inject_styles__$q=undefined;/* scoped */var __vue_scope_id__$q=undefined;/* module identifier */var __vue_module_identifier__$q=undefined;/* functional template */var __vue_is_functional_template__$q=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var SummaryRow=normalizeComponent({render:__vue_render__$q,staticRenderFns:__vue_staticRenderFns__$q},__vue_inject_styles__$q,__vue_script__$q,__vue_scope_id__$q,__vue_is_functional_template__$q,__vue_module_identifier__$q,false,undefined,undefined,undefined);//
var script$r={components:{ContentHeader:ContentHeader,FooterNav:FooterNav,Modal:Modal,Sidebar:Sidebar,SummaryRow:SummaryRow},computed:_objectSpread({},index_esm.mapGetters(['allowManualRetry','canStartUpload','cropFiles','cropForce','disableTransformer','fileCount','maxFiles','filesDone','filesFailed','filesUploading','filesList','filesPaused','filesWaiting','filesNeededCount','isConnected','isConnectedObj','lang','minFiles','mobileNavActive','route','routesHistory','uploadStarted']),{fileBuckets:function fileBuckets(){if(this.uploadStarted){if(!this.isConnected&&this.allowManualRetry){return[{id:'failed',name:'Connection Lost',files:this.filesPaused.concat(this.filesFailed)},{id:'done',name:'Completed',files:this.filesDone},{id:'uploading',name:'Uploading',files:this.filesUploading.concat(this.filesWaiting)}];}return[{id:'failed',name:'Failed While Uploading',files:this.filesFailed},{id:'done',name:'Completed',files:this.filesDone},{id:'uploading',name:'Uploading',files:this.filesUploading.concat(this.filesWaiting)}];}return[{id:'edited',name:'Edited Images',files:this.onlyTransformedImages},{id:'cropped',name:'Cropped Images',files:this.onlyCroppedImages},{id:'images',name:'Images',files:this.onlyImages},{id:'files',name:'Files',files:this.onlyFiles}];},minFilesMessage:function minFilesMessage(){if(this.filesNeededCount===1){return"".concat(this.t('Add')," 1 ").concat(this.t('more file'));}if(this.filesNeededCount>1){return"".concat(this.t('Add')," ").concat(this.filesNeededCount," ").concat(this.t('more files'));}return null;},onlyFiles:function onlyFiles(){return this.filesList.filter(function(f){return!_isImage(f);});},onlyImages:function onlyImages(){return this.filesList.filter(function(f){return _isImage(f);}).filter(function(f){return!f.transformed;});},onlyTransformedImages:function onlyTransformedImages(){if(this.cropForce){return this.filesList.filter(function(f){return _isImage(f);}).filter(function(f){return f.transformed;}).filter(function(f){return!f.cropped;});}return this.filesList.filter(function(f){return _isImage(f);}).filter(function(f){return f.transformed;});},onlyCroppedImages:function onlyCroppedImages(){if(this.cropForce){return this.filesList.filter(function(f){return _isImage(f);}).filter(function(f){return f.transformed;}).filter(function(f){return f.cropped;});}return[];},cropForceComplete:function cropForceComplete(){if(this.cropForce){return!this.onlyImages.length&&!this.onlyTransformedImages.length;}return true;},headerText:function headerText(){if(this.uploadStarted){return"".concat(this.t('Uploaded')," ").concat(this.filesDone.length," / ").concat(this.fileCount);}if(this.cropForce&&!this.cropForceComplete){return this.t('Crop is required on images');}return this.t('Selected Files');},placeholderText:function placeholderText(){return this.t('Filter');},reconnectTimer:function reconnectTimer(){return"- Retrying in ".concat(this.timer.toLocaleString(this.lang),"...");}}),data:function data(){return{timer:20,filter:''};},methods:_objectSpread({},index_esm.mapActions(['addFile','deselectAllFiles','retryAllFailedFiles','startUploading','transformImage']),{filterFiles:function filterFiles(filesList){var pattern=new RegExp(this.filter,'i');return filesList.filter(function(f){return pattern.test(f.name);});},retryAll:function retryAll(){this.resetCountdown();this.retryAllFailedFiles();this.$store.commit('SET_CONNECTION_STATUS',true);this.$store.commit('RESET_ATTEMPTS');},startCountdown:function startCountdown(){var _this31=this;if(!this.timerInterval){this.timerInterval=setInterval(function(){_this31.timer-=1;},1000);}},resetCountdown:function resetCountdown(){clearInterval(this.timerInterval);this.timerInterval=null;this.timer=20;},startUploadMaybe:function startUploadMaybe(){if(this.canStartUpload&&this.cropForceComplete){this.startUploading();}if(!this.cropForceComplete){var image=this.onlyImages[0]||this.onlyTransformedImages[0];this.transformImage(image.uuid);}},updateFilter:function updateFilter(event){this.filter=event.target.value;},historyBack:function historyBack(){this.$store.commit('GO_BACK_WITH_ROUTE_CURRENT_TYPE');}}),watch:{isConnectedObj:{handler:function handler(connected){if(connected.value===false){this.startCountdown();}if(connected.value===true){this.resetCountdown();}}},timer:{handler:function handler(value){if(value===0){this.retryAll();}}},filesList:{immediate:false,handler:function handler(files){// Go back when there is nothing left in the current view
if(files.length===0){if(this.cropFiles){this.$store.dispatch('cancelPick');this.$root.$destroy();}else{this.$store.commit('GO_BACK_WITH_ROUTE_CURRENT_TYPE');this.$store.commit('SET_UPLOAD_STARTED',false);}}}}}};/* script */var __vue_script__$r=script$r;/* template */var __vue_render__$r=function __vue_render__$r(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("modal",[_c("div",{attrs:{slot:"header"},slot:"header"},[_c("content-header",[!_vm.mobileNavActive?_c("span",{staticClass:"fsp-header-text--visible"},[_vm._v("\n        "+_vm._s(_vm.headerText)+"\n      ")]):_vm._e()])],1),_vm._v(" "),_c("sidebar",{attrs:{slot:"sidebar"},slot:"sidebar"}),_vm._v(" "),_c("div",{staticClass:"fsp-summary",attrs:{slot:"body"},slot:"body"},[_c("div",{staticClass:"fsp-summary__header"},[_c("div",{staticClass:"fsp-summary__filter"},[_c("input",{attrs:{placeholder:_vm.placeholderText},on:{input:_vm.updateFilter}}),_vm._v(" "),_c("span",{staticClass:"fsp-summary__filter-icon"})])]),_vm._v(" "),_c("div",{staticClass:"fsp-summary__body"},_vm._l(_vm.fileBuckets,function(bucket){return _vm.filterFiles(bucket.files).length?_c("div",{key:bucket.id},[_c("div",{staticClass:"fsp-grid__label","class":{"fsp-color--error":bucket.id==="failed"}},[_vm._v("\n            "+_vm._s(_vm.t(bucket.name))+"\n            "),_vm.isConnected&&_vm.allowManualRetry&&bucket.id==="failed"?_c("span",{staticClass:"fsp-color--error fsp-summary__try-again",on:{click:_vm.retryAll}},[_vm._v("\n            "+_vm._s(_vm.t("Try again"))+"\n            ")]):!_vm.isConnected&&_vm.allowManualRetry&&bucket.id==="failed"?_c("span",{staticClass:"fsp-color--error",on:{click:_vm.retryAll}},[_vm._v("\n              "+_vm._s(_vm.reconnectTimer)+"\n              "),_c("span",{staticClass:"fsp-summary__try-again"},[_vm._v(_vm._s(_vm.t("Try now")))])]):_vm._e()]),_vm._v(" "),_vm._l(_vm.filterFiles(bucket.files),function(file){return _c("SummaryRow",{key:file.uuid,attrs:{file:file}});})],2):_vm._e();}),0)]),_vm._v(" "),_c("footer-nav",{attrs:{slot:"footer","is-visible":!_vm.uploadStarted},slot:"footer"},[_c("span",{staticClass:"fsp-button fsp-button--cancel",attrs:{slot:"nav-left",tabindex:"0"},on:{click:_vm.deselectAllFiles,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.deselectAllFiles($event);}},slot:"nav-left"},[_vm._v("\n      "+_vm._s(_vm.t("Deselect All"))+"\n    ")]),_vm._v(" "),_c("div",{attrs:{slot:"nav-right"},slot:"nav-right"},[_vm.fileCount<_vm.maxFiles?_c("span",{staticClass:"fsp-button fsp-button--cancel",attrs:{"data-e2e":"upload-more",tabindex:"0"},on:{click:_vm.historyBack,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.historyBack($event);}}},[_vm._v("\n        "+_vm._s(_vm.t("Upload more"))+"\n      ")]):_vm._e(),_vm._v(" "),_c("span",{staticClass:"fsp-button fsp-button--primary fsp-button-upload","class":{"fsp-button--disabled":!_vm.canStartUpload},attrs:{"data-e2e":"upload",title:_vm.t("Upload"),tabindex:"0"},on:{click:_vm.startUploadMaybe,keyup:function keyup($event){if(!$event.type.indexOf("key")&&_vm._k($event.keyCode,"enter",13,$event.key,"Enter")){return null;}return _vm.startUploadMaybe($event);}}},[!_vm.uploadStarted&&!_vm.canStartUpload?_c("span",[_vm._v("\n          "+_vm._s(_vm.minFilesMessage)+"\n        ")]):_c("span",[_vm._v("\n          "+_vm._s(_vm.t("Upload"))+"\n          "),_c("span",{directives:[{name:"show",rawName:"v-show",value:_vm.filesWaiting.length>1,expression:"filesWaiting.length > 1"}],staticClass:"fsp-badge fsp-badge--bright"},[_vm._v("\n            "+_vm._s(_vm.filesWaiting.length)+"\n          ")])])])])])],1);};var __vue_staticRenderFns__$r=[];__vue_render__$r._withStripped=true;/* style */var __vue_inject_styles__$r=undefined;/* scoped */var __vue_scope_id__$r=undefined;/* module identifier */var __vue_module_identifier__$r=undefined;/* functional template */var __vue_is_functional_template__$r=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var UploadSummary=normalizeComponent({render:__vue_render__$r,staticRenderFns:__vue_staticRenderFns__$r},__vue_inject_styles__$r,__vue_script__$r,__vue_scope_id__$r,__vue_is_functional_template__$r,__vue_module_identifier__$r,false,undefined,undefined,undefined);/*! *****************************************************************************
  Copyright (c) Microsoft Corporation. All rights reserved.
  Licensed under the Apache License, Version 2.0 (the "License"); you may not use
  this file except in compliance with the License. You may obtain a copy of the
  License at http://www.apache.org/licenses/LICENSE-2.0

  THIS CODE IS PROVIDED ON AN *AS IS* BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, EITHER EXPRESS OR IMPLIED, INCLUDING WITHOUT LIMITATION ANY IMPLIED
  WARRANTIES OR CONDITIONS OF TITLE, FITNESS FOR A PARTICULAR PURPOSE,
  MERCHANTABLITY OR NON-INFRINGEMENT.

  See the Apache Version 2.0 License for specific language governing permissions
  and limitations under the License.
  ***************************************************************************** */ /* global Reflect, Promise */var _extendStatics=function extendStatics(d,b){_extendStatics=Object.setPrototypeOf||{__proto__:[]}instanceof Array&&function(d,b){d.__proto__=b;}||function(d,b){for(var p in b){if(b.hasOwnProperty(p))d[p]=b[p];}};return _extendStatics(d,b);};function __extends(d,b){_extendStatics(d,b);function __(){this.constructor=d;}d.prototype=b===null?Object.create(b):(__.prototype=b.prototype,new __());}var _assign=function __assign(){_assign=Object.assign||function __assign(t){for(var s,i=1,n=arguments.length;i<n;i++){s=arguments[i];for(var p in s){if(Object.prototype.hasOwnProperty.call(s,p))t[p]=s[p];}}return t;};return _assign.apply(this,arguments);};function __awaiter(thisArg,_arguments,P,generator){return new(P||(P=Promise))(function(resolve,reject){function fulfilled(value){try{step(generator.next(value));}catch(e){reject(e);}}function rejected(value){try{step(generator["throw"](value));}catch(e){reject(e);}}function step(result){result.done?resolve(result.value):new P(function(resolve){resolve(result.value);}).then(fulfilled,rejected);}step((generator=generator.apply(thisArg,_arguments||[])).next());});}function __generator(thisArg,body){var _={label:0,sent:function sent(){if(t[0]&1)throw t[1];return t[1];},trys:[],ops:[]},f,y,t,g;return g={next:verb(0),"throw":verb(1),"return":verb(2)},typeof Symbol==="function"&&(g[Symbol.iterator]=function(){return this;}),g;function verb(n){return function(v){return step([n,v]);};}function step(op){if(f)throw new TypeError("Generator is already executing.");while(_){try{if(f=1,y&&(t=op[0]&2?y["return"]:op[0]?y["throw"]||((t=y["return"])&&t.call(y),0):y.next)&&!(t=t.call(y,op[1])).done)return t;if(y=0,t)op=[op[0]&2,t.value];switch(op[0]){case 0:case 1:t=op;break;case 4:_.label++;return{value:op[1],done:false};case 5:_.label++;y=op[1];op=[0];continue;case 7:op=_.ops.pop();_.trys.pop();continue;default:if(!(t=_.trys,t=t.length>0&&t[t.length-1])&&(op[0]===6||op[0]===2)){_=0;continue;}if(op[0]===3&&(!t||op[1]>t[0]&&op[1]<t[3])){_.label=op[1];break;}if(op[0]===6&&_.label<t[1]){_.label=t[1];t=op;break;}if(t&&_.label<t[2]){_.label=t[2];_.ops.push(op);break;}if(t[2])_.ops.pop();_.trys.pop();continue;}op=body.call(thisArg,_);}catch(e){op=[6,e];y=0;}finally{f=t=0;}}if(op[0]&5)throw op[1];return{value:op[0]?op[1]:void 0,done:true};}}function __read(o,n){var m=typeof Symbol==="function"&&o[Symbol.iterator];if(!m)return o;var i=m.call(o),r,ar=[],e;try{while((n===void 0||n-->0)&&!(r=i.next()).done){ar.push(r.value);}}catch(error){e={error:error};}finally{try{if(r&&!r.done&&(m=i["return"]))m.call(i);}finally{if(e)throw e.error;}}return ar;}function __spread(){for(var ar=[],i=0;i<arguments.length;i++){ar=ar.concat(__read(arguments[i]));}return ar;}/** JSDoc */var Severity;(function(Severity){/** JSDoc */Severity["Fatal"]="fatal";/** JSDoc */Severity["Error"]="error";/** JSDoc */Severity["Warning"]="warning";/** JSDoc */Severity["Log"]="log";/** JSDoc */Severity["Info"]="info";/** JSDoc */Severity["Debug"]="debug";/** JSDoc */Severity["Critical"]="critical";})(Severity||(Severity={}));// tslint:disable:completed-docs
// tslint:disable:no-unnecessary-qualifier no-namespace
(function(Severity){/**
       * Converts a string-based level into a {@link Severity}.
       *
       * @param level string representation of Severity
       * @returns Severity
       */function fromString(level){switch(level){case'debug':return Severity.Debug;case'info':return Severity.Info;case'warn':case'warning':return Severity.Warning;case'error':return Severity.Error;case'fatal':return Severity.Fatal;case'critical':return Severity.Critical;case'log':default:return Severity.Log;}}Severity.fromString=fromString;})(Severity||(Severity={}));/** The status of an event. */var Status;(function(Status){/** The status could not be determined. */Status["Unknown"]="unknown";/** The event was skipped due to configuration or callbacks. */Status["Skipped"]="skipped";/** The event was sent to Sentry successfully. */Status["Success"]="success";/** The client is currently rate limited and will try again later. */Status["RateLimit"]="rate_limit";/** The event could not be processed. */Status["Invalid"]="invalid";/** A server-side error ocurred during submission. */Status["Failed"]="failed";})(Status||(Status={}));// tslint:disable:completed-docs
// tslint:disable:no-unnecessary-qualifier no-namespace
(function(Status){/**
       * Converts a HTTP status code into a {@link Status}.
       *
       * @param code The HTTP response status code.
       * @returns The send status or {@link Status.Unknown}.
       */function fromHttpCode(code){if(code>=200&&code<300){return Status.Success;}if(code===429){return Status.RateLimit;}if(code>=400&&code<500){return Status.Invalid;}if(code>=500){return Status.Failed;}return Status.Unknown;}Status.fromHttpCode=fromHttpCode;})(Status||(Status={}));var setPrototypeOf=Object.setPrototypeOf||({__proto__:[]}instanceof Array?setProtoOf:mixinProperties);// tslint:disable-line:no-unbound-method
/**
   * setPrototypeOf polyfill using __proto__
   */function setProtoOf(obj,proto){// @ts-ignore
obj.__proto__=proto;return obj;}/**
   * setPrototypeOf polyfill using mixin
   */function mixinProperties(obj,proto){for(var prop in proto){if(!obj.hasOwnProperty(prop)){// @ts-ignore
obj[prop]=proto[prop];}}return obj;}/** An error emitted by Sentry SDKs and related utilities. */var SentryError=/** @class */function(_super){__extends(SentryError,_super);function SentryError(message){var _newTarget=this.constructor;var _this=_super.call(this,message)||this;_this.message=message;// tslint:disable:no-unsafe-any
_this.name=_newTarget.prototype.constructor.name;setPrototypeOf(_this,_newTarget.prototype);return _this;}return SentryError;}(Error);/**
   * Checks whether given value's type is one of a few Error or Error-like
   * {@link isError}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isError(wat){switch(Object.prototype.toString.call(wat)){case'[object Error]':return true;case'[object Exception]':return true;case'[object DOMException]':return true;default:return wat instanceof Error;}}/**
   * Checks whether given value's type is ErrorEvent
   * {@link isErrorEvent}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isErrorEvent(wat){return Object.prototype.toString.call(wat)==='[object ErrorEvent]';}/**
   * Checks whether given value's type is DOMError
   * {@link isDOMError}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isDOMError(wat){return Object.prototype.toString.call(wat)==='[object DOMError]';}/**
   * Checks whether given value's type is DOMException
   * {@link isDOMException}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isDOMException(wat){return Object.prototype.toString.call(wat)==='[object DOMException]';}/**
   * Checks whether given value's type is a string
   * {@link isString}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isString(wat){return Object.prototype.toString.call(wat)==='[object String]';}/**
   * Checks whether given value's is a primitive (undefined, null, number, boolean, string)
   * {@link isPrimitive}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isPrimitive$1(wat){return wat===null||_typeof2(wat)!=='object'&&typeof wat!=='function';}/**
   * Checks whether given value's type is an object literal
   * {@link isPlainObject}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isPlainObject$1(wat){return Object.prototype.toString.call(wat)==='[object Object]';}/**
   * Checks whether given value's type is an Event instance
   * {@link isEvent}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isEvent(wat){// tslint:disable-next-line:strict-type-predicates
return typeof Event!=='undefined'&&wat instanceof Event;}/**
   * Checks whether given value's type is an Element instance
   * {@link isElement}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isElement(wat){// tslint:disable-next-line:strict-type-predicates
return typeof Element!=='undefined'&&wat instanceof Element;}/**
   * Checks whether given value's type is an regexp
   * {@link isRegExp}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isRegExp$1(wat){return Object.prototype.toString.call(wat)==='[object RegExp]';}/**
   * Checks whether given value has a then function.
   * @param wat A value to be checked.
   */function isThenable(wat){// tslint:disable:no-unsafe-any
return Boolean(wat&&wat.then&&typeof wat.then==='function');// tslint:enable:no-unsafe-any
}/**
   * Checks whether given value's type is a SyntheticEvent
   * {@link isSyntheticEvent}.
   *
   * @param wat A value to be checked.
   * @returns A boolean representing the result.
   */function isSyntheticEvent(wat){// tslint:disable-next-line:no-unsafe-any
return isPlainObject$1(wat)&&'nativeEvent'in wat&&'preventDefault'in wat&&'stopPropagation'in wat;}/**
   * Requires a module which is protected _against bundler minification.
   *
   * @param request The module path to resolve
   */function dynamicRequire(mod,request){// tslint:disable-next-line: no-unsafe-any
return mod.require(request);}/**
   * Checks whether we're in the Node.js or Browser environment
   *
   * @returns Answer to given question
   */function isNodeEnv(){// tslint:disable:strict-type-predicates
return Object.prototype.toString.call(typeof process!=='undefined'?process:0)==='[object process]';}var fallbackGlobalObject={};/**
   * Safely get global scope object
   *
   * @returns Global scope object
   */function getGlobalObject(){return isNodeEnv()?global:typeof window!=='undefined'?window:typeof self!=='undefined'?self:fallbackGlobalObject;}/**
   * UUID4 generator
   *
   * @returns string Generated UUID4.
   */function uuid4(){var global=getGlobalObject();var crypto=global.crypto||global.msCrypto;if(!(crypto===void 0)&&crypto.getRandomValues){// Use window.crypto API if available
var arr=new Uint16Array(8);crypto.getRandomValues(arr);// set 4 in byte 7
// tslint:disable-next-line:no-bitwise
arr[3]=arr[3]&0xfff|0x4000;// set 2 most significant bits of byte 9 to '10'
// tslint:disable-next-line:no-bitwise
arr[4]=arr[4]&0x3fff|0x8000;var pad=function pad(num){var v=num.toString(16);while(v.length<4){v="0"+v;}return v;};return pad(arr[0])+pad(arr[1])+pad(arr[2])+pad(arr[3])+pad(arr[4])+pad(arr[5])+pad(arr[6])+pad(arr[7]);}// http://stackoverflow.com/questions/105034/how-to-create-a-guid-uuid-in-javascript/2117523#2117523
return'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g,function(c){// tslint:disable-next-line:no-bitwise
var r=Math.random()*16|0;// tslint:disable-next-line:no-bitwise
var v=c==='x'?r:r&0x3|0x8;return v.toString(16);});}/**
   * Parses string form of URL into an object
   * // borrowed from https://tools.ietf.org/html/rfc3986#appendix-B
   * // intentionally using regex and not <a/> href parsing trick because React Native and other
   * // environments where DOM might not be available
   * @returns parsed URL object
   */function parseUrl(url){if(!url){return{};}var match=url.match(/^(([^:\/?#]+):)?(\/\/([^\/?#]*))?([^?#]*)(\?([^#]*))?(#(.*))?$/);if(!match){return{};}// coerce to undefined values to empty string so we don't get 'undefined'
var query=match[6]||'';var fragment=match[8]||'';return{host:match[4],path:match[5],protocol:match[2],relative:match[5]+query+fragment};}/**
   * Extracts either message or type+value from an event that can be used for user-facing logs
   * @returns event's description
   */function getEventDescription(event){if(event.message){return event.message;}if(event.exception&&event.exception.values&&event.exception.values[0]){var exception=event.exception.values[0];if(exception.type&&exception.value){return exception.type+": "+exception.value;}return exception.type||exception.value||event.event_id||'<unknown>';}return event.event_id||'<unknown>';}/** JSDoc */function consoleSandbox(callback){var global=getGlobalObject();var levels=['debug','info','warn','error','log','assert'];if(!('console'in global)){return callback();}var originalConsole=global.console;var wrappedLevels={};// Restore all wrapped console methods
levels.forEach(function(level){if(level in global.console&&originalConsole[level].__sentry__){wrappedLevels[level]=originalConsole[level].__sentry_wrapped__;originalConsole[level]=originalConsole[level].__sentry_original__;}});// Perform callback manipulations
var result=callback();// Revert restoration to wrapped state
Object.keys(wrappedLevels).forEach(function(level){originalConsole[level]=wrappedLevels[level];});return result;}/**
   * Adds exception values, type and value to an synthetic Exception.
   * @param event The event to modify.
   * @param value Value of the exception.
   * @param type Type of the exception.
   * @hidden
   */function addExceptionTypeValue(event,value,type){event.exception=event.exception||{};event.exception.values=event.exception.values||[];event.exception.values[0]=event.exception.values[0]||{};event.exception.values[0].value=event.exception.values[0].value||value||'';event.exception.values[0].type=event.exception.values[0].type||type||'Error';}/**
   * Adds exception mechanism to a given event.
   * @param event The event to modify.
   * @param mechanism Mechanism of the mechanism.
   * @hidden
   */function addExceptionMechanism(event,mechanism){if(mechanism===void 0){mechanism={};}// TODO: Use real type with `keyof Mechanism` thingy and maybe make it better?
try{// @ts-ignore
// tslint:disable:no-non-null-assertion
event.exception.values[0].mechanism=event.exception.values[0].mechanism||{};Object.keys(mechanism).forEach(function(key){// @ts-ignore
event.exception.values[0].mechanism[key]=mechanism[key];});}catch(_oO){// no-empty
}}/**
   * A safe form of location.href
   */function getLocationHref(){try{return document.location.href;}catch(oO){return'';}}/**
   * Given a child DOM element, returns a query-selector statement describing that
   * and its ancestors
   * e.g. [HTMLElement] => body > div > input#foo.btn[name=baz]
   * @returns generated DOM path
   */function htmlTreeAsString(elem){// try/catch both:
// - accessing event.target (see getsentry/raven-js#838, #768)
// - `htmlTreeAsString` because it's complex, and just accessing the DOM incorrectly
// - can throw an exception in some circumstances.
try{var currentElem=elem;var MAX_TRAVERSE_HEIGHT=5;var MAX_OUTPUT_LEN=80;var out=[];var height=0;var len=0;var separator=' > ';var sepLength=separator.length;var nextStr=void 0;while(currentElem&&height++<MAX_TRAVERSE_HEIGHT){nextStr=_htmlElementAsString(currentElem);// bail out if
// - nextStr is the 'html' element
// - the length of the string that would be created exceeds MAX_OUTPUT_LEN
//   (ignore this limit if we are on the first iteration)
if(nextStr==='html'||height>1&&len+out.length*sepLength+nextStr.length>=MAX_OUTPUT_LEN){break;}out.push(nextStr);len+=nextStr.length;currentElem=currentElem.parentNode;}return out.reverse().join(separator);}catch(_oO){return'<unknown>';}}/**
   * Returns a simple, query-selector representation of a DOM element
   * e.g. [HTMLElement] => input#foo.btn[name=baz]
   * @returns generated DOM path
   */function _htmlElementAsString(el){var elem=el;var out=[];var className;var classes;var key;var attr;var i;if(!elem||!elem.tagName){return'';}out.push(elem.tagName.toLowerCase());if(elem.id){out.push("#"+elem.id);}className=elem.className;if(className&&isString(className)){classes=className.split(/\s+/);for(i=0;i<classes.length;i++){out.push("."+classes[i]);}}var attrWhitelist=['type','name','title','alt'];for(i=0;i<attrWhitelist.length;i++){key=attrWhitelist[i];attr=elem.getAttribute(key);if(attr){out.push("["+key+"=\""+attr+"\"]");}}return out.join('');}var defaultRetryAfter=60*1000;// 60 seconds
/**
   * Extracts Retry-After value from the request header or returns default value
   * @param now current unix timestamp
   * @param header string representation of 'Retry-After' header
   */function parseRetryAfterHeader(now,header){if(!header){return defaultRetryAfter;}var headerDelay=parseInt(""+header,10);if(!isNaN(headerDelay)){return headerDelay*1000;}var headerDate=Date.parse(""+header);if(!isNaN(headerDate)){return headerDate-now;}return defaultRetryAfter;}// TODO: Implement different loggers for different environments
var global$1=getGlobalObject();/** Prefix for logging strings */var PREFIX='Sentry Logger ';/** JSDoc */var Logger=/** @class */function(){/** JSDoc */function Logger(){this._enabled=false;}/** JSDoc */Logger.prototype.disable=function(){this._enabled=false;};/** JSDoc */Logger.prototype.enable=function(){this._enabled=true;};/** JSDoc */Logger.prototype.log=function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}if(!this._enabled){return;}consoleSandbox(function(){global$1.console.log(PREFIX+"[Log]: "+args.join(' '));// tslint:disable-line:no-console
});};/** JSDoc */Logger.prototype.warn=function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}if(!this._enabled){return;}consoleSandbox(function(){global$1.console.warn(PREFIX+"[Warn]: "+args.join(' '));// tslint:disable-line:no-console
});};/** JSDoc */Logger.prototype.error=function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}if(!this._enabled){return;}consoleSandbox(function(){global$1.console.error(PREFIX+"[Error]: "+args.join(' '));// tslint:disable-line:no-console
});};return Logger;}();// Ensure we only have a single logger instance, even if multiple versions of @sentry/utils are being used
global$1.__SENTRY__=global$1.__SENTRY__||{};var logger$1=global$1.__SENTRY__.logger||(global$1.__SENTRY__.logger=new Logger());// tslint:disable:no-unsafe-any
/**
   * Memo class used for decycle json objects. Uses WeakSet if available otherwise array.
   */var Memo=/** @class */function(){function Memo(){// tslint:disable-next-line
this._hasWeakSet=typeof WeakSet==='function';this._inner=this._hasWeakSet?new WeakSet():[];}/**
       * Sets obj to remember.
       * @param obj Object to remember
       */Memo.prototype.memoize=function(obj){if(this._hasWeakSet){if(this._inner.has(obj)){return true;}this._inner.add(obj);return false;}// tslint:disable-next-line:prefer-for-of
for(var i=0;i<this._inner.length;i++){var value=this._inner[i];if(value===obj){return true;}}this._inner.push(obj);return false;};/**
       * Removes object from internal storage.
       * @param obj Object to forget
       */Memo.prototype.unmemoize=function(obj){if(this._hasWeakSet){this._inner["delete"](obj);}else{for(var i=0;i<this._inner.length;i++){if(this._inner[i]===obj){this._inner.splice(i,1);break;}}}};return Memo;}();/**
   * Truncates given string to the maximum characters count
   *
   * @param str An object that contains serializable values
   * @param max Maximum number of characters in truncated string
   * @returns string Encoded
   */function truncate(str,max){if(max===void 0){max=0;}// tslint:disable-next-line:strict-type-predicates
if(typeof str!=='string'||max===0){return str;}return str.length<=max?str:str.substr(0,max)+"...";}/**
   * Join values in array
   * @param input array of values to be joined together
   * @param delimiter string to be placed in-between values
   * @returns Joined values
   */function safeJoin(input,delimiter){if(!Array.isArray(input)){return'';}var output=[];// tslint:disable-next-line:prefer-for-of
for(var i=0;i<input.length;i++){var value=input[i];try{output.push(String(value));}catch(e){output.push('[value cannot be serialized]');}}return output.join(delimiter);}/**
   * Checks if the value matches a regex or includes the string
   * @param value The string value to be checked against
   * @param pattern Either a regex or a string that must be contained in value
   */function isMatchingPattern(value,pattern){if(isRegExp$1(pattern)){return pattern.test(value);}if(typeof pattern==='string'){return value.indexOf(pattern)!==-1;}return false;}/**
   * Wrap a given object method with a higher-order function
   *
   * @param source An object that contains a method to be wrapped.
   * @param name A name of method to be wrapped.
   * @param replacement A function that should be used to wrap a given method.
   * @returns void
   */function fill(source,name,replacement){if(!(name in source)){return;}var original=source[name];var wrapped=replacement(original);// Make sure it's a function first, as we need to attach an empty prototype for `defineProperties` to work
// otherwise it'll throw "TypeError: Object.defineProperties called on non-object"
// tslint:disable-next-line:strict-type-predicates
if(typeof wrapped==='function'){try{wrapped.prototype=wrapped.prototype||{};Object.defineProperties(wrapped,{__sentry__:{enumerable:false,value:true},__sentry_original__:{enumerable:false,value:original},__sentry_wrapped__:{enumerable:false,value:wrapped}});}catch(_Oo){// This can throw if multiple fill happens on a global object like XMLHttpRequest
// Fixes https://github.com/getsentry/sentry-javascript/issues/2043
}}source[name]=wrapped;}/**
   * Encodes given object into url-friendly format
   *
   * @param object An object that contains serializable values
   * @returns string Encoded
   */function urlEncode(object){return Object.keys(object).map(// tslint:disable-next-line:no-unsafe-any
function(key){return encodeURIComponent(key)+"="+encodeURIComponent(object[key]);}).join('&');}/**
   * Transforms any object into an object literal with all it's attributes
   * attached to it.
   *
   * @param value Initial source that we have to transform in order to be usable by the serializer
   */function getWalkSource(value){if(isError(value)){var error=value;var err={message:error.message,name:error.name,stack:error.stack};for(var i in error){if(Object.prototype.hasOwnProperty.call(error,i)){err[i]=error[i];}}return err;}if(isEvent(value)){var event_1=value;var source={};source.type=event_1.type;// Accessing event.target can throw (see getsentry/raven-js#838, #768)
try{source.target=isElement(event_1.target)?htmlTreeAsString(event_1.target):Object.prototype.toString.call(event_1.target);}catch(_oO){source.target='<unknown>';}try{source.currentTarget=isElement(event_1.currentTarget)?htmlTreeAsString(event_1.currentTarget):Object.prototype.toString.call(event_1.currentTarget);}catch(_oO){source.currentTarget='<unknown>';}// tslint:disable-next-line:strict-type-predicates
if(typeof CustomEvent!=='undefined'&&value instanceof CustomEvent){source.detail=event_1.detail;}for(var i in event_1){if(Object.prototype.hasOwnProperty.call(event_1,i)){source[i]=event_1;}}return source;}return value;}/** Calculates bytes size of input string */function utf8Length(value){// tslint:disable-next-line:no-bitwise
return~-encodeURI(value).split(/%..|./).length;}/** Calculates bytes size of input object */function jsonSize(value){return utf8Length(JSON.stringify(value));}/** JSDoc */function normalizeToSize(object,// Default Node.js REPL depth
depth,// 100kB, as 200kB is max payload size, so half sounds reasonable
maxSize){if(depth===void 0){depth=3;}if(maxSize===void 0){maxSize=100*1024;}var serialized=normalize$1(object,depth);if(jsonSize(serialized)>maxSize){return normalizeToSize(object,depth-1,maxSize);}return serialized;}/** Transforms any input value into a string form, either primitive value or a type of the input */function serializeValue(value){var type=Object.prototype.toString.call(value);// Node.js REPL notation
if(typeof value==='string'){return value;}if(type==='[object Object]'){return'[Object]';}if(type==='[object Array]'){return'[Array]';}var normalized=normalizeValue(value);return isPrimitive$1(normalized)?normalized:type;}/**
   * normalizeValue()
   *
   * Takes unserializable input and make it serializable friendly
   *
   * - translates undefined/NaN values to "[undefined]"/"[NaN]" respectively,
   * - serializes Error objects
   * - filter global objects
   */ // tslint:disable-next-line:cyclomatic-complexity
function normalizeValue(value,key){if(key==='domain'&&value&&_typeof2(value)==='object'&&value._events){return'[Domain]';}if(key==='domainEmitter'){return'[DomainEmitter]';}if(typeof global!=='undefined'&&value===global){return'[Global]';}if(typeof window!=='undefined'&&value===window){return'[Window]';}if(typeof document!=='undefined'&&value===document){return'[Document]';}// React's SyntheticEvent thingy
if(isSyntheticEvent(value)){return'[SyntheticEvent]';}// tslint:disable-next-line:no-tautology-expression
if(typeof value==='number'&&value!==value){return'[NaN]';}if(value===void 0){return'[undefined]';}if(typeof value==='function'){return"[Function: "+(value.name||'<unknown-function-name>')+"]";}return value;}/**
   * Walks an object to perform a normalization on it
   *
   * @param key of object that's walked in current iteration
   * @param value object to be walked
   * @param depth Optional number indicating how deep should walking be performed
   * @param memo Optional Memo class handling decycling
   */function walk(key,value,depth,memo){if(depth===void 0){depth=+Infinity;}if(memo===void 0){memo=new Memo();}// If we reach the maximum depth, serialize whatever has left
if(depth===0){return serializeValue(value);}// If value implements `toJSON` method, call it and return early
// tslint:disable:no-unsafe-any
if(value!==null&&value!==undefined&&typeof value.toJSON==='function'){return value.toJSON();}// tslint:enable:no-unsafe-any
// If normalized value is a primitive, there are no branches left to walk, so we can just bail out, as theres no point in going down that branch any further
var normalized=normalizeValue(value,key);if(isPrimitive$1(normalized)){return normalized;}// Create source that we will use for next itterations, either objectified error object (Error type with extracted keys:value pairs) or the input itself
var source=getWalkSource(value);// Create an accumulator that will act as a parent for all future itterations of that branch
var acc=Array.isArray(value)?[]:{};// If we already walked that branch, bail out, as it's circular reference
if(memo.memoize(value)){return'[Circular ~]';}// Walk all keys of the source
for(var innerKey in source){// Avoid iterating over fields in the prototype if they've somehow been exposed to enumeration.
if(!Object.prototype.hasOwnProperty.call(source,innerKey)){continue;}// Recursively walk through all the child nodes
acc[innerKey]=walk(innerKey,source[innerKey],depth-1,memo);}// Once walked through all the branches, remove the parent from memo storage
memo.unmemoize(value);// Return accumulated values
return acc;}/**
   * normalize()
   *
   * - Creates a copy to prevent original input mutation
   * - Skip non-enumerablers
   * - Calls `toJSON` if implemented
   * - Removes circular references
   * - Translates non-serializeable values (undefined/NaN/Functions) to serializable format
   * - Translates known global objects/Classes to a string representations
   * - Takes care of Error objects serialization
   * - Optionally limit depth of final output
   */function normalize$1(input,depth){try{// tslint:disable-next-line:no-unsafe-any
return JSON.parse(JSON.stringify(input,function(key,value){return walk(key,value,depth);}));}catch(_oO){return'**non-serializable**';}}/**
   * Given any captured exception, extract its keys and create a sorted
   * and truncated list that will be used inside the event message.
   * eg. `Non-error exception captured with keys: foo, bar, baz`
   */function extractExceptionKeysForMessage(exception,maxLength){if(maxLength===void 0){maxLength=40;}// tslint:disable:strict-type-predicates
var keys=Object.keys(getWalkSource(exception));keys.sort();if(!keys.length){return'[object has no keys]';}if(keys[0].length>=maxLength){return truncate(keys[0],maxLength);}for(var includedKeys=keys.length;includedKeys>0;includedKeys--){var serialized=keys.slice(0,includedKeys).join(', ');if(serialized.length>maxLength){continue;}if(includedKeys===keys.length){return serialized;}return truncate(serialized,maxLength);}return'';}/** SyncPromise internal states */var States;(function(States){/** Pending */States["PENDING"]="PENDING";/** Resolved / OK */States["RESOLVED"]="RESOLVED";/** Rejected / Error */States["REJECTED"]="REJECTED";})(States||(States={}));/**
   * Thenable class that behaves like a Promise and follows it's interface
   * but is not async internally
   */var SyncPromise=/** @class */function(){function SyncPromise(executor){var _this=this;this._state=States.PENDING;this._handlers=[];/** JSDoc */this._resolve=function(value){_this._setResult(States.RESOLVED,value);};/** JSDoc */this._reject=function(reason){_this._setResult(States.REJECTED,reason);};/** JSDoc */this._setResult=function(state,value){if(_this._state!==States.PENDING){return;}if(isThenable(value)){value.then(_this._resolve,_this._reject);return;}_this._state=state;_this._value=value;_this._executeHandlers();};// TODO: FIXME
/** JSDoc */this._attachHandler=function(handler){_this._handlers=_this._handlers.concat(handler);_this._executeHandlers();};/** JSDoc */this._executeHandlers=function(){if(_this._state===States.PENDING){return;}if(_this._state===States.REJECTED){_this._handlers.forEach(function(handler){if(handler.onrejected){handler.onrejected(_this._value);}});}else{_this._handlers.forEach(function(handler){if(handler.onfulfilled){// tslint:disable-next-line:no-unsafe-any
handler.onfulfilled(_this._value);}});}_this._handlers=[];};try{executor(this._resolve,this._reject);}catch(e){this._reject(e);}}/** JSDoc */SyncPromise.prototype.toString=function(){return'[object SyncPromise]';};/** JSDoc */SyncPromise.resolve=function(value){return new SyncPromise(function(resolve){resolve(value);});};/** JSDoc */SyncPromise.reject=function(reason){return new SyncPromise(function(_,reject){reject(reason);});};/** JSDoc */SyncPromise.all=function(collection){return new SyncPromise(function(resolve,reject){if(!Array.isArray(collection)){reject(new TypeError("Promise.all requires an array as input."));return;}if(collection.length===0){resolve([]);return;}var counter=collection.length;var resolvedCollection=[];collection.forEach(function(item,index){SyncPromise.resolve(item).then(function(value){resolvedCollection[index]=value;counter-=1;if(counter!==0){return;}resolve(resolvedCollection);}).then(null,reject);});});};/** JSDoc */SyncPromise.prototype.then=function(_onfulfilled,_onrejected){var _this=this;return new SyncPromise(function(resolve,reject){_this._attachHandler({onfulfilled:function onfulfilled(result){if(!_onfulfilled){// TODO: ¯\_(ツ)_/¯
// TODO: FIXME
resolve(result);return;}try{resolve(_onfulfilled(result));return;}catch(e){reject(e);return;}},onrejected:function onrejected(reason){if(!_onrejected){reject(reason);return;}try{resolve(_onrejected(reason));return;}catch(e){reject(e);return;}}});});};/** JSDoc */SyncPromise.prototype["catch"]=function(onrejected){return this.then(function(val){return val;},onrejected);};/** JSDoc */SyncPromise.prototype["finally"]=function(onfinally){var _this=this;return new SyncPromise(function(resolve,reject){var val;var isRejected;return _this.then(function(value){isRejected=false;val=value;if(onfinally){onfinally();}},function(reason){isRejected=true;val=reason;if(onfinally){onfinally();}}).then(function(){if(isRejected){reject(val);return;}// tslint:disable-next-line:no-unsafe-any
resolve(val);});});};return SyncPromise;}();/** A simple queue that holds promises. */var PromiseBuffer=/** @class */function(){function PromiseBuffer(_limit){this._limit=_limit;/** Internal set of queued Promises */this._buffer=[];}/**
       * Says if the buffer is ready to take more requests
       */PromiseBuffer.prototype.isReady=function(){return this._limit===undefined||this.length()<this._limit;};/**
       * Add a promise to the queue.
       *
       * @param task Can be any PromiseLike<T>
       * @returns The original promise.
       */PromiseBuffer.prototype.add=function(task){var _this=this;if(!this.isReady()){return SyncPromise.reject(new SentryError('Not adding Promise due to buffer limit reached.'));}if(this._buffer.indexOf(task)===-1){this._buffer.push(task);}task.then(function(){return _this.remove(task);}).then(null,function(){return _this.remove(task).then(null,function(){// We have to add this catch here otherwise we have an unhandledPromiseRejection
// because it's a new Promise chain.
});});return task;};/**
       * Remove a promise to the queue.
       *
       * @param task Can be any PromiseLike<T>
       * @returns Removed promise.
       */PromiseBuffer.prototype.remove=function(task){var removedTask=this._buffer.splice(this._buffer.indexOf(task),1)[0];return removedTask;};/**
       * This function returns the number of unresolved promises in the queue.
       */PromiseBuffer.prototype.length=function(){return this._buffer.length;};/**
       * This will drain the whole queue, returns true if queue is empty or drained.
       * If timeout is provided and the queue takes longer to drain, the promise still resolves but with false.
       *
       * @param timeout Number in ms to wait until it resolves with false.
       */PromiseBuffer.prototype.drain=function(timeout){var _this=this;return new SyncPromise(function(resolve){var capturedSetTimeout=setTimeout(function(){if(timeout&&timeout>0){resolve(false);}},timeout);SyncPromise.all(_this._buffer).then(function(){clearTimeout(capturedSetTimeout);resolve(true);}).then(null,function(){resolve(true);});});};return PromiseBuffer;}();/**
   * Tells whether current environment supports Fetch API
   * {@link supportsFetch}.
   *
   * @returns Answer to the given question.
   */function supportsFetch(){if(!('fetch'in getGlobalObject())){return false;}try{// tslint:disable-next-line:no-unused-expression
new Headers();// tslint:disable-next-line:no-unused-expression
new Request('');// tslint:disable-next-line:no-unused-expression
new Response();return true;}catch(e){return false;}}/**
   * Tells whether current environment supports Fetch API natively
   * {@link supportsNativeFetch}.
   *
   * @returns true if `window.fetch` is natively implemented, false otherwise
   */function supportsNativeFetch(){if(!supportsFetch()){return false;}var isNativeFunc=function isNativeFunc(func){return func.toString().indexOf('native')!==-1;};var global=getGlobalObject();var result=null;var doc=global.document;if(doc){var sandbox=doc.createElement('iframe');sandbox.hidden=true;try{doc.head.appendChild(sandbox);if(sandbox.contentWindow&&sandbox.contentWindow.fetch){// tslint:disable-next-line no-unbound-method
result=isNativeFunc(sandbox.contentWindow.fetch);}doc.head.removeChild(sandbox);}catch(err){logger$1.warn('Could not create sandbox iframe for pure fetch check, bailing to window.fetch: ',err);}}if(result===null){// tslint:disable-next-line no-unbound-method
result=isNativeFunc(global.fetch);}return result;}/**
   * Tells whether current environment supports Referrer Policy API
   * {@link supportsReferrerPolicy}.
   *
   * @returns Answer to the given question.
   */function supportsReferrerPolicy(){// Despite all stars in the sky saying that Edge supports old draft syntax, aka 'never', 'always', 'origin' and 'default
// https://caniuse.com/#feat=referrer-policy
// It doesn't. And it throw exception instead of ignoring this parameter...
// REF: https://github.com/getsentry/raven-js/issues/1233
if(!supportsFetch()){return false;}try{// tslint:disable:no-unused-expression
new Request('_',{referrerPolicy:'origin'});return true;}catch(e){return false;}}/**
   * Tells whether current environment supports History API
   * {@link supportsHistory}.
   *
   * @returns Answer to the given question.
   */function supportsHistory(){// NOTE: in Chrome App environment, touching history.pushState, *even inside
//       a try/catch block*, will cause Chrome to output an error to console.error
// borrowed from: https://github.com/angular/angular.js/pull/13945/files
var global=getGlobalObject();var chrome=global.chrome;// tslint:disable-next-line:no-unsafe-any
var isChromePackagedApp=chrome&&chrome.app&&chrome.app.runtime;var hasHistoryApi='history'in global&&!!global.history.pushState&&!!global.history.replaceState;return!isChromePackagedApp&&hasHistoryApi;}var TRACEPARENT_REGEXP=/^[ \t]*([0-9a-f]{32})?-?([0-9a-f]{16})?-?([01])?[ \t]*$/;/**
   * Span containg all data about a span
   */var Span=/** @class */function(){function Span(_traceId,_spanId,_sampled,_parent){if(_traceId===void 0){_traceId=uuid4();}if(_spanId===void 0){_spanId=uuid4().substring(16);}this._traceId=_traceId;this._spanId=_spanId;this._sampled=_sampled;this._parent=_parent;}/**
       * Setter for parent
       */Span.prototype.setParent=function(parent){this._parent=parent;return this;};/**
       * Setter for sampled
       */Span.prototype.setSampled=function(sampled){this._sampled=sampled;return this;};/**
       * Continues a trace
       * @param traceparent Traceparent string
       */Span.fromTraceparent=function(traceparent){var matches=traceparent.match(TRACEPARENT_REGEXP);if(matches){var sampled=void 0;if(matches[3]==='1'){sampled=true;}else if(matches[3]==='0'){sampled=false;}var parent_1=new Span(matches[1],matches[2],sampled);return new Span(matches[1],undefined,sampled,parent_1);}return undefined;};/**
       * @inheritDoc
       */Span.prototype.toTraceparent=function(){var sampled='';if(this._sampled===true){sampled='-1';}else if(this._sampled===false){sampled='-0';}return this._traceId+"-"+this._spanId+sampled;};/**
       * @inheritDoc
       */Span.prototype.toJSON=function(){return{parent:this._parent&&this._parent.toJSON()||undefined,sampled:this._sampled,span_id:this._spanId,trace_id:this._traceId};};return Span;}();/**
   * Holds additional event information. {@link Scope.applyToEvent} will be
   * called by the client before an event will be sent.
   */var Scope=/** @class */function(){function Scope(){/** Flag if notifiying is happening. */this._notifyingListeners=false;/** Callback for client to receive scope changes. */this._scopeListeners=[];/** Callback list that will be called after {@link applyToEvent}. */this._eventProcessors=[];/** Array of breadcrumbs. */this._breadcrumbs=[];/** User */this._user={};/** Tags */this._tags={};/** Extra */this._extra={};/** Contexts */this._context={};}/**
       * Add internal on change listener. Used for sub SDKs that need to store the scope.
       * @hidden
       */Scope.prototype.addScopeListener=function(callback){this._scopeListeners.push(callback);};/**
       * @inheritDoc
       */Scope.prototype.addEventProcessor=function(callback){this._eventProcessors.push(callback);return this;};/**
       * This will be called on every set call.
       */Scope.prototype._notifyScopeListeners=function(){var _this=this;if(!this._notifyingListeners){this._notifyingListeners=true;setTimeout(function(){_this._scopeListeners.forEach(function(callback){callback(_this);});_this._notifyingListeners=false;});}};/**
       * This will be called after {@link applyToEvent} is finished.
       */Scope.prototype._notifyEventProcessors=function(processors,event,hint,index){var _this=this;if(index===void 0){index=0;}return new SyncPromise(function(resolve,reject){var processor=processors[index];// tslint:disable-next-line:strict-type-predicates
if(event===null||typeof processor!=='function'){resolve(event);}else{var result=processor(_assign({},event),hint);if(isThenable(result)){result.then(function(_final){return _this._notifyEventProcessors(processors,_final,hint,index+1).then(resolve);}).then(null,reject);}else{_this._notifyEventProcessors(processors,result,hint,index+1).then(resolve).then(null,reject);}}});};/**
       * @inheritDoc
       */Scope.prototype.setUser=function(user){this._user=normalize$1(user);this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setTags=function(tags){this._tags=_assign({},this._tags,normalize$1(tags));this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setTag=function(key,value){var _a;this._tags=_assign({},this._tags,(_a={},_a[key]=normalize$1(value),_a));this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setExtras=function(extra){this._extra=_assign({},this._extra,normalize$1(extra));this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setExtra=function(key,extra){var _a;this._extra=_assign({},this._extra,(_a={},_a[key]=normalize$1(extra),_a));this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setFingerprint=function(fingerprint){this._fingerprint=normalize$1(fingerprint);this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setLevel=function(level){this._level=normalize$1(level);this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setTransaction=function(transaction){this._transaction=transaction;this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setContext=function(name,context){this._context[name]=context?normalize$1(context):undefined;this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.setSpan=function(span){this._span=span;this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.startSpan=function(parentSpan){var span=new Span();span.setParent(parentSpan);this.setSpan(span);return span;};/**
       * Internal getter for Span, used in Hub.
       * @hidden
       */Scope.prototype.getSpan=function(){return this._span;};/**
       * Inherit values from the parent scope.
       * @param scope to clone.
       */Scope.clone=function(scope){var newScope=new Scope();if(scope){newScope._breadcrumbs=__spread(scope._breadcrumbs);newScope._tags=_assign({},scope._tags);newScope._extra=_assign({},scope._extra);newScope._context=_assign({},scope._context);newScope._user=scope._user;newScope._level=scope._level;newScope._span=scope._span;newScope._transaction=scope._transaction;newScope._fingerprint=scope._fingerprint;newScope._eventProcessors=__spread(scope._eventProcessors);}return newScope;};/**
       * @inheritDoc
       */Scope.prototype.clear=function(){this._breadcrumbs=[];this._tags={};this._extra={};this._user={};this._context={};this._level=undefined;this._transaction=undefined;this._fingerprint=undefined;this._span=undefined;this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.addBreadcrumb=function(breadcrumb,maxBreadcrumbs){var timestamp=new Date().getTime()/1000;var mergedBreadcrumb=_assign({timestamp:timestamp},breadcrumb);this._breadcrumbs=maxBreadcrumbs!==undefined&&maxBreadcrumbs>=0?__spread(this._breadcrumbs,[normalize$1(mergedBreadcrumb)]).slice(-maxBreadcrumbs):__spread(this._breadcrumbs,[normalize$1(mergedBreadcrumb)]);this._notifyScopeListeners();return this;};/**
       * @inheritDoc
       */Scope.prototype.clearBreadcrumbs=function(){this._breadcrumbs=[];this._notifyScopeListeners();return this;};/**
       * Applies fingerprint from the scope to the event if there's one,
       * uses message if there's one instead or get rid of empty fingerprint
       */Scope.prototype._applyFingerprint=function(event){// Make sure it's an array first and we actually have something in place
event.fingerprint=event.fingerprint?Array.isArray(event.fingerprint)?event.fingerprint:[event.fingerprint]:[];// If we have something on the scope, then merge it with event
if(this._fingerprint){event.fingerprint=event.fingerprint.concat(this._fingerprint);}// If we have no data at all, remove empty array default
if(event.fingerprint&&!event.fingerprint.length){delete event.fingerprint;}};/**
       * Applies the current context and fingerprint to the event.
       * Note that breadcrumbs will be added by the client.
       * Also if the event has already breadcrumbs on it, we do not merge them.
       * @param event Event
       * @param hint May contain additional informartion about the original exception.
       * @hidden
       */Scope.prototype.applyToEvent=function(event,hint){if(this._extra&&Object.keys(this._extra).length){event.extra=_assign({},this._extra,event.extra);}if(this._tags&&Object.keys(this._tags).length){event.tags=_assign({},this._tags,event.tags);}if(this._user&&Object.keys(this._user).length){event.user=_assign({},this._user,event.user);}if(this._context&&Object.keys(this._context).length){event.contexts=_assign({},this._context,event.contexts);}if(this._level){event.level=this._level;}if(this._transaction){event.transaction=this._transaction;}if(this._span){event.contexts=event.contexts||{};event.contexts.trace=this._span;}this._applyFingerprint(event);event.breadcrumbs=__spread(event.breadcrumbs||[],this._breadcrumbs);event.breadcrumbs=event.breadcrumbs.length>0?event.breadcrumbs:undefined;return this._notifyEventProcessors(__spread(getGlobalEventProcessors(),this._eventProcessors),event,hint);};return Scope;}();/**
   * Retruns the global event processors.
   */function getGlobalEventProcessors(){var global=getGlobalObject();global.__SENTRY__=global.__SENTRY__||{};global.__SENTRY__.globalEventProcessors=global.__SENTRY__.globalEventProcessors||[];return global.__SENTRY__.globalEventProcessors;}/**
   * Add a EventProcessor to be kept globally.
   * @param callback EventProcessor to add
   */function addGlobalEventProcessor(callback){getGlobalEventProcessors().push(callback);}/**
   * API compatibility version of this hub.
   *
   * WARNING: This number should only be incresed when the global interface
   * changes a and new methods are introduced.
   *
   * @hidden
   */var API_VERSION=3;/**
   * Default maximum number of breadcrumbs added to an event. Can be overwritten
   * with {@link Options.maxBreadcrumbs}.
   */var DEFAULT_BREADCRUMBS=30;/**
   * Absolute maximum number of breadcrumbs added to an event. The
   * `maxBreadcrumbs` option cannot be higher than this value.
   */var MAX_BREADCRUMBS=100;/**
   * @inheritDoc
   */var Hub=/** @class */function(){/**
       * Creates a new instance of the hub, will push one {@link Layer} into the
       * internal stack on creation.
       *
       * @param client bound to the hub.
       * @param scope bound to the hub.
       * @param version number, higher number means higher priority.
       */function Hub(client,scope,_version){if(scope===void 0){scope=new Scope();}if(_version===void 0){_version=API_VERSION;}this._version=_version;/** Is a {@link Layer}[] containing the client and scope */this._stack=[];this._stack.push({client:client,scope:scope});}/**
       * Internal helper function to call a method on the top client if it exists.
       *
       * @param method The method to call on the client.
       * @param args Arguments to pass to the client function.
       */Hub.prototype._invokeClient=function(method){var _a;var args=[];for(var _i=1;_i<arguments.length;_i++){args[_i-1]=arguments[_i];}var top=this.getStackTop();if(top&&top.client&&top.client[method]){(_a=top.client)[method].apply(_a,__spread(args,[top.scope]));}};/**
       * @inheritDoc
       */Hub.prototype.isOlderThan=function(version){return this._version<version;};/**
       * @inheritDoc
       */Hub.prototype.bindClient=function(client){var top=this.getStackTop();top.client=client;};/**
       * @inheritDoc
       */Hub.prototype.pushScope=function(){// We want to clone the content of prev scope
var stack=this.getStack();var parentScope=stack.length>0?stack[stack.length-1].scope:undefined;var scope=Scope.clone(parentScope);this.getStack().push({client:this.getClient(),scope:scope});return scope;};/**
       * @inheritDoc
       */Hub.prototype.popScope=function(){return this.getStack().pop()!==undefined;};/**
       * @inheritDoc
       */Hub.prototype.withScope=function(callback){var scope=this.pushScope();try{callback(scope);}finally{this.popScope();}};/**
       * @inheritDoc
       */Hub.prototype.getClient=function(){return this.getStackTop().client;};/** Returns the scope of the top stack. */Hub.prototype.getScope=function(){return this.getStackTop().scope;};/** Returns the scope stack for domains or the process. */Hub.prototype.getStack=function(){return this._stack;};/** Returns the topmost scope layer in the order domain > local > process. */Hub.prototype.getStackTop=function(){return this._stack[this._stack.length-1];};/**
       * @inheritDoc
       */Hub.prototype.captureException=function(exception,hint){var eventId=this._lastEventId=uuid4();var finalHint=hint;// If there's no explicit hint provided, mimick the same thing that would happen
// in the minimal itself to create a consistent behavior.
// We don't do this in the client, as it's the lowest level API, and doing this,
// would prevent user from having full control over direct calls.
if(!hint){var syntheticException=void 0;try{throw new Error('Sentry syntheticException');}catch(exception){syntheticException=exception;}finalHint={originalException:exception,syntheticException:syntheticException};}this._invokeClient('captureException',exception,_assign({},finalHint,{event_id:eventId}));return eventId;};/**
       * @inheritDoc
       */Hub.prototype.captureMessage=function(message,level,hint){var eventId=this._lastEventId=uuid4();var finalHint=hint;// If there's no explicit hint provided, mimick the same thing that would happen
// in the minimal itself to create a consistent behavior.
// We don't do this in the client, as it's the lowest level API, and doing this,
// would prevent user from having full control over direct calls.
if(!hint){var syntheticException=void 0;try{throw new Error(message);}catch(exception){syntheticException=exception;}finalHint={originalException:message,syntheticException:syntheticException};}this._invokeClient('captureMessage',message,level,_assign({},finalHint,{event_id:eventId}));return eventId;};/**
       * @inheritDoc
       */Hub.prototype.captureEvent=function(event,hint){var eventId=this._lastEventId=uuid4();this._invokeClient('captureEvent',event,_assign({},hint,{event_id:eventId}));return eventId;};/**
       * @inheritDoc
       */Hub.prototype.lastEventId=function(){return this._lastEventId;};/**
       * @inheritDoc
       */Hub.prototype.addBreadcrumb=function(breadcrumb,hint){var top=this.getStackTop();if(!top.scope||!top.client){return;}var _a=top.client.getOptions&&top.client.getOptions()||{},_b=_a.beforeBreadcrumb,beforeBreadcrumb=_b===void 0?null:_b,_c=_a.maxBreadcrumbs,maxBreadcrumbs=_c===void 0?DEFAULT_BREADCRUMBS:_c;if(maxBreadcrumbs<=0){return;}var timestamp=new Date().getTime()/1000;var mergedBreadcrumb=_assign({timestamp:timestamp},breadcrumb);var finalBreadcrumb=beforeBreadcrumb?consoleSandbox(function(){return beforeBreadcrumb(mergedBreadcrumb,hint);}):mergedBreadcrumb;if(finalBreadcrumb===null){return;}top.scope.addBreadcrumb(finalBreadcrumb,Math.min(maxBreadcrumbs,MAX_BREADCRUMBS));};/**
       * @inheritDoc
       */Hub.prototype.setUser=function(user){var top=this.getStackTop();if(!top.scope){return;}top.scope.setUser(user);};/**
       * @inheritDoc
       */Hub.prototype.setTags=function(tags){var top=this.getStackTop();if(!top.scope){return;}top.scope.setTags(tags);};/**
       * @inheritDoc
       */Hub.prototype.setExtras=function(extras){var top=this.getStackTop();if(!top.scope){return;}top.scope.setExtras(extras);};/**
       * @inheritDoc
       */Hub.prototype.setTag=function(key,value){var top=this.getStackTop();if(!top.scope){return;}top.scope.setTag(key,value);};/**
       * @inheritDoc
       */Hub.prototype.setExtra=function(key,extra){var top=this.getStackTop();if(!top.scope){return;}top.scope.setExtra(key,extra);};/**
       * @inheritDoc
       */Hub.prototype.setContext=function(name,context){var top=this.getStackTop();if(!top.scope){return;}top.scope.setContext(name,context);};/**
       * @inheritDoc
       */Hub.prototype.configureScope=function(callback){var top=this.getStackTop();if(top.scope&&top.client){callback(top.scope);}};/**
       * @inheritDoc
       */Hub.prototype.run=function(callback){var oldHub=makeMain(this);try{callback(this);}finally{makeMain(oldHub);}};/**
       * @inheritDoc
       */Hub.prototype.getIntegration=function(integration){var client=this.getClient();if(!client){return null;}try{return client.getIntegration(integration);}catch(_oO){logger$1.warn("Cannot retrieve integration "+integration.id+" from the current Hub");return null;}};/**
       * @inheritDoc
       */Hub.prototype.traceHeaders=function(){var top=this.getStackTop();if(top.scope&&top.client){var span=top.scope.getSpan();if(span){return{'sentry-trace':span.toTraceparent()};}}return{};};return Hub;}();/** Returns the global shim registry. */function getMainCarrier(){var carrier=getGlobalObject();carrier.__SENTRY__=carrier.__SENTRY__||{hub:undefined};return carrier;}/**
   * Replaces the current main hub with the passed one on the global object
   *
   * @returns The old replaced hub
   */function makeMain(hub){var registry=getMainCarrier();var oldHub=getHubFromCarrier(registry);setHubOnCarrier(registry,hub);return oldHub;}/**
   * Returns the default hub instance.
   *
   * If a hub is already registered in the global carrier but this module
   * contains a more recent version, it replaces the registered version.
   * Otherwise, the currently registered hub will be returned.
   */function getCurrentHub(){// Get main carrier (global for every environment)
var registry=getMainCarrier();// If there's no hub, or its an old API, assign a new one
if(!hasHubOnCarrier(registry)||getHubFromCarrier(registry).isOlderThan(API_VERSION)){setHubOnCarrier(registry,new Hub());}// Prefer domains over global if they are there (applicable only to Node environment)
if(isNodeEnv()){return getHubFromActiveDomain(registry);}// Return hub that lives on a global object
return getHubFromCarrier(registry);}/**
   * Try to read the hub from an active domain, fallback to the registry if one doesnt exist
   * @returns discovered hub
   */function getHubFromActiveDomain(registry){try{// We need to use `dynamicRequire` because `require` on it's own will be optimized by webpack.
// We do not want this to happen, we need to try to `require` the domain node module and fail if we are in browser
// for example so we do not have to shim it and use `getCurrentHub` universally.
var domain=dynamicRequire(module,'domain');var activeDomain=domain.active;// If there no active domain, just return global hub
if(!activeDomain){return getHubFromCarrier(registry);}// If there's no hub on current domain, or its an old API, assign a new one
if(!hasHubOnCarrier(activeDomain)||getHubFromCarrier(activeDomain).isOlderThan(API_VERSION)){var registryHubTopStack=getHubFromCarrier(registry).getStackTop();setHubOnCarrier(activeDomain,new Hub(registryHubTopStack.client,Scope.clone(registryHubTopStack.scope)));}// Return hub that lives on a domain
return getHubFromCarrier(activeDomain);}catch(_Oo){// Return hub that lives on a global object
return getHubFromCarrier(registry);}}/**
   * This will tell whether a carrier has a hub on it or not
   * @param carrier object
   */function hasHubOnCarrier(carrier){if(carrier&&carrier.__SENTRY__&&carrier.__SENTRY__.hub){return true;}return false;}/**
   * This will create a new {@link Hub} and add to the passed object on
   * __SENTRY__.hub.
   * @param carrier object
   * @hidden
   */function getHubFromCarrier(carrier){if(carrier&&carrier.__SENTRY__&&carrier.__SENTRY__.hub){return carrier.__SENTRY__.hub;}carrier.__SENTRY__=carrier.__SENTRY__||{};carrier.__SENTRY__.hub=new Hub();return carrier.__SENTRY__.hub;}/**
   * This will set passed {@link Hub} on the passed object's __SENTRY__.hub attribute
   * @param carrier object
   * @param hub Hub
   */function setHubOnCarrier(carrier,hub){if(!carrier){return false;}carrier.__SENTRY__=carrier.__SENTRY__||{};carrier.__SENTRY__.hub=hub;return true;}/**
   * This calls a function on the current hub.
   * @param method function to call on hub.
   * @param args to pass to function.
   */function callOnHub(method){var args=[];for(var _i=1;_i<arguments.length;_i++){args[_i-1]=arguments[_i];}var hub=getCurrentHub();if(hub&&hub[method]){// tslint:disable-next-line:no-unsafe-any
return hub[method].apply(hub,__spread(args));}throw new Error("No hub defined or "+method+" was not found on the hub, please open a bug report.");}/**
   * Captures an exception event and sends it to Sentry.
   *
   * @param exception An exception-like object.
   * @returns The generated eventId.
   */function captureException(exception){var syntheticException;try{throw new Error('Sentry syntheticException');}catch(exception){syntheticException=exception;}return callOnHub('captureException',exception,{originalException:exception,syntheticException:syntheticException});}/**
   * Captures a message event and sends it to Sentry.
   *
   * @param message The message to send to Sentry.
   * @param level Define the level of the message.
   * @returns The generated eventId.
   */function captureMessage(message,level){var syntheticException;try{throw new Error(message);}catch(exception){syntheticException=exception;}return callOnHub('captureMessage',message,level,{originalException:message,syntheticException:syntheticException});}/**
   * Captures a manually created event and sends it to Sentry.
   *
   * @param event The event to send to Sentry.
   * @returns The generated eventId.
   */function captureEvent(event){return callOnHub('captureEvent',event);}/**
   * Callback to set context information onto the scope.
   * @param callback Callback function that receives Scope.
   */function configureScope(callback){callOnHub('configureScope',callback);}/**
   * Records a new breadcrumb which will be attached to future events.
   *
   * Breadcrumbs will be added to subsequent events to provide more context on
   * user's actions prior to an error or crash.
   *
   * @param breadcrumb The breadcrumb to record.
   */function addBreadcrumb(breadcrumb){callOnHub('addBreadcrumb',breadcrumb);}/**
   * Sets context data with the given name.
   * @param name of the context
   * @param context Any kind of data. This data will be normailzed.
   */function setContext(name,context){callOnHub('setContext',name,context);}/**
   * Set an object that will be merged sent as extra data with the event.
   * @param extras Extras object to merge into current context.
   */function setExtras(extras){callOnHub('setExtras',extras);}/**
   * Set an object that will be merged sent as tags data with the event.
   * @param tags Tags context object to merge into current context.
   */function setTags(tags){callOnHub('setTags',tags);}/**
   * Set key:value that will be sent as extra data with the event.
   * @param key String of extra
   * @param extra Any kind of data. This data will be normailzed.
   */function setExtra(key,extra){callOnHub('setExtra',key,extra);}/**
   * Set key:value that will be sent as tags data with the event.
   * @param key String key of tag
   * @param value String value of tag
   */function setTag(key,value){callOnHub('setTag',key,value);}/**
   * Updates user context information for future events.
   *
   * @param user User context object to be set in the current context. Pass `null` to unset the user.
   */function setUser(user){callOnHub('setUser',user);}/**
   * Creates a new scope with and executes the given operation within.
   * The scope is automatically removed once the operation
   * finishes or throws.
   *
   * This is essentially a convenience function for:
   *
   *     pushScope();
   *     callback();
   *     popScope();
   *
   * @param callback that will be enclosed into push/popScope.
   */function withScope(callback){callOnHub('withScope',callback);}/** Regular expression used to parse a Dsn. */var DSN_REGEX=/^(?:(\w+):)\/\/(?:(\w+)(?::(\w+))?@)([\w\.-]+)(?::(\d+))?\/(.+)/;/** Error message */var ERROR_MESSAGE='Invalid Dsn';/** The Sentry Dsn, identifying a Sentry instance and project. */var Dsn=/** @class */function(){/** Creates a new Dsn component */function Dsn(from){if(typeof from==='string'){this._fromString(from);}else{this._fromComponents(from);}this._validate();}/**
       * Renders the string representation of this Dsn.
       *
       * By default, this will render the public representation without the password
       * component. To get the deprecated private _representation, set `withPassword`
       * to true.
       *
       * @param withPassword When set to true, the password will be included.
       */Dsn.prototype.toString=function(withPassword){if(withPassword===void 0){withPassword=false;}// tslint:disable-next-line:no-this-assignment
var _a=this,host=_a.host,path=_a.path,pass=_a.pass,port=_a.port,projectId=_a.projectId,protocol=_a.protocol,user=_a.user;return protocol+"://"+user+(withPassword&&pass?":"+pass:'')+("@"+host+(port?":"+port:'')+"/"+(path?path+"/":path)+projectId);};/** Parses a string into this Dsn. */Dsn.prototype._fromString=function(str){var match=DSN_REGEX.exec(str);if(!match){throw new SentryError(ERROR_MESSAGE);}var _a=__read(match.slice(1),6),protocol=_a[0],user=_a[1],_b=_a[2],pass=_b===void 0?'':_b,host=_a[3],_c=_a[4],port=_c===void 0?'':_c,lastPath=_a[5];var path='';var projectId=lastPath;var split=projectId.split('/');if(split.length>1){path=split.slice(0,-1).join('/');projectId=split.pop();}this._fromComponents({host:host,pass:pass,path:path,projectId:projectId,port:port,protocol:protocol,user:user});};/** Maps Dsn components into this instance. */Dsn.prototype._fromComponents=function(components){this.protocol=components.protocol;this.user=components.user;this.pass=components.pass||'';this.host=components.host;this.port=components.port||'';this.path=components.path||'';this.projectId=components.projectId;};/** Validates this Dsn and throws on error. */Dsn.prototype._validate=function(){var _this=this;['protocol','user','host','projectId'].forEach(function(component){if(!_this[component]){throw new SentryError(ERROR_MESSAGE);}});if(this.protocol!=='http'&&this.protocol!=='https'){throw new SentryError(ERROR_MESSAGE);}if(this.port&&isNaN(parseInt(this.port,10))){throw new SentryError(ERROR_MESSAGE);}};return Dsn;}();var SENTRY_API_VERSION='7';/** Helper class to provide urls to different Sentry endpoints. */var API=/** @class */function(){/** Create a new instance of API */function API(dsn){this.dsn=dsn;this._dsnObject=new Dsn(dsn);}/** Returns the Dsn object. */API.prototype.getDsn=function(){return this._dsnObject;};/** Returns a string with auth headers in the url to the store endpoint. */API.prototype.getStoreEndpoint=function(){return""+this._getBaseUrl()+this.getStoreEndpointPath();};/** Returns the store endpoint with auth added in url encoded. */API.prototype.getStoreEndpointWithUrlEncodedAuth=function(){var dsn=this._dsnObject;var auth={sentry_key:dsn.user,sentry_version:SENTRY_API_VERSION};// Auth is intentionally sent as part of query string (NOT as custom HTTP header)
// to avoid preflight CORS requests
return this.getStoreEndpoint()+"?"+urlEncode(auth);};/** Returns the base path of the url including the port. */API.prototype._getBaseUrl=function(){var dsn=this._dsnObject;var protocol=dsn.protocol?dsn.protocol+":":'';var port=dsn.port?":"+dsn.port:'';return protocol+"//"+dsn.host+port;};/** Returns only the path component for the store endpoint. */API.prototype.getStoreEndpointPath=function(){var dsn=this._dsnObject;return(dsn.path?"/"+dsn.path:'')+"/api/"+dsn.projectId+"/store/";};/** Returns an object that can be used in request headers. */API.prototype.getRequestHeaders=function(clientName,clientVersion){var dsn=this._dsnObject;var header=["Sentry sentry_version="+SENTRY_API_VERSION];header.push("sentry_timestamp="+new Date().getTime());header.push("sentry_client="+clientName+"/"+clientVersion);header.push("sentry_key="+dsn.user);if(dsn.pass){header.push("sentry_secret="+dsn.pass);}return{'Content-Type':'application/json','X-Sentry-Auth':header.join(', ')};};/** Returns the url to the report dialog endpoint. */API.prototype.getReportDialogEndpoint=function(dialogOptions){if(dialogOptions===void 0){dialogOptions={};}var dsn=this._dsnObject;var endpoint=""+this._getBaseUrl()+(dsn.path?"/"+dsn.path:'')+"/api/embed/error-page/";var encodedOptions=[];encodedOptions.push("dsn="+dsn.toString());for(var key in dialogOptions){if(key==='user'){if(!dialogOptions.user){continue;}if(dialogOptions.user.name){encodedOptions.push("name="+encodeURIComponent(dialogOptions.user.name));}if(dialogOptions.user.email){encodedOptions.push("email="+encodeURIComponent(dialogOptions.user.email));}}else{encodedOptions.push(encodeURIComponent(key)+"="+encodeURIComponent(dialogOptions[key]));}}if(encodedOptions.length){return endpoint+"?"+encodedOptions.join('&');}return endpoint;};return API;}();var installedIntegrations=[];/** Gets integration to install */function getIntegrationsToSetup(options){var defaultIntegrations=options.defaultIntegrations&&__spread(options.defaultIntegrations)||[];var userIntegrations=options.integrations;var integrations=[];if(Array.isArray(userIntegrations)){var userIntegrationsNames_1=userIntegrations.map(function(i){return i.name;});var pickedIntegrationsNames_1=[];// Leave only unique default integrations, that were not overridden with provided user integrations
defaultIntegrations.forEach(function(defaultIntegration){if(userIntegrationsNames_1.indexOf(defaultIntegration.name)===-1&&pickedIntegrationsNames_1.indexOf(defaultIntegration.name)===-1){integrations.push(defaultIntegration);pickedIntegrationsNames_1.push(defaultIntegration.name);}});// Don't add same user integration twice
userIntegrations.forEach(function(userIntegration){if(pickedIntegrationsNames_1.indexOf(userIntegration.name)===-1){integrations.push(userIntegration);pickedIntegrationsNames_1.push(userIntegration.name);}});}else if(typeof userIntegrations==='function'){integrations=userIntegrations(defaultIntegrations);integrations=Array.isArray(integrations)?integrations:[integrations];}else{integrations=__spread(defaultIntegrations);}// Make sure that if present, `Debug` integration will always run last
var integrationsNames=integrations.map(function(i){return i.name;});var alwaysLastToRun='Debug';if(integrationsNames.indexOf(alwaysLastToRun)!==-1){integrations.push.apply(integrations,__spread(integrations.splice(integrationsNames.indexOf(alwaysLastToRun),1)));}return integrations;}/** Setup given integration */function setupIntegration(integration){if(installedIntegrations.indexOf(integration.name)!==-1){return;}integration.setupOnce(addGlobalEventProcessor,getCurrentHub);installedIntegrations.push(integration.name);logger$1.log("Integration installed: "+integration.name);}/**
   * Given a list of integration instances this installs them all. When `withDefaults` is set to `true` then all default
   * integrations are added unless they were already provided before.
   * @param integrations array of integration instances
   * @param withDefault should enable default integrations
   */function setupIntegrations(options){var integrations={};getIntegrationsToSetup(options).forEach(function(integration){integrations[integration.name]=integration;setupIntegration(integration);});return integrations;}/**
   * Base implementation for all JavaScript SDK clients.
   *
   * Call the constructor with the corresponding backend constructor and options
   * specific to the client subclass. To access these options later, use
   * {@link Client.getOptions}. Also, the Backend instance is available via
   * {@link Client.getBackend}.
   *
   * If a Dsn is specified in the options, it will be parsed and stored. Use
   * {@link Client.getDsn} to retrieve the Dsn at any moment. In case the Dsn is
   * invalid, the constructor will throw a {@link SentryException}. Note that
   * without a valid Dsn, the SDK will not send any events to Sentry.
   *
   * Before sending an event via the backend, it is passed through
   * {@link BaseClient.prepareEvent} to add SDK information and scope data
   * (breadcrumbs and context). To add more custom information, override this
   * method and extend the resulting prepared event.
   *
   * To issue automatically created events (e.g. via instrumentation), use
   * {@link Client.captureEvent}. It will prepare the event and pass it through
   * the callback lifecycle. To issue auto-breadcrumbs, use
   * {@link Client.addBreadcrumb}.
   *
   * @example
   * class NodeClient extends BaseClient<NodeBackend, NodeOptions> {
   *   public constructor(options: NodeOptions) {
   *     super(NodeBackend, options);
   *   }
   *
   *   // ...
   * }
   */var BaseClient=/** @class */function(){/**
       * Initializes this client instance.
       *
       * @param backendClass A constructor function to create the backend.
       * @param options Options for the client.
       */function BaseClient(backendClass,options){/** Array of used integrations. */this._integrations={};/** Is the client still processing a call? */this._processing=false;this._backend=new backendClass(options);this._options=options;if(options.dsn){this._dsn=new Dsn(options.dsn);}if(this._isEnabled()){this._integrations=setupIntegrations(this._options);}}/**
       * @inheritDoc
       */BaseClient.prototype.captureException=function(exception,hint,scope){var _this=this;var eventId=hint&&hint.event_id;this._processing=true;this._getBackend().eventFromException(exception,hint).then(function(event){return _this._processEvent(event,hint,scope);}).then(function(finalEvent){// We need to check for finalEvent in case beforeSend returned null
eventId=finalEvent&&finalEvent.event_id;_this._processing=false;}).then(null,function(reason){logger$1.error(reason);_this._processing=false;});return eventId;};/**
       * @inheritDoc
       */BaseClient.prototype.captureMessage=function(message,level,hint,scope){var _this=this;var eventId=hint&&hint.event_id;this._processing=true;var promisedEvent=isPrimitive$1(message)?this._getBackend().eventFromMessage(""+message,level,hint):this._getBackend().eventFromException(message,hint);promisedEvent.then(function(event){return _this._processEvent(event,hint,scope);}).then(function(finalEvent){// We need to check for finalEvent in case beforeSend returned null
eventId=finalEvent&&finalEvent.event_id;_this._processing=false;}).then(null,function(reason){logger$1.error(reason);_this._processing=false;});return eventId;};/**
       * @inheritDoc
       */BaseClient.prototype.captureEvent=function(event,hint,scope){var _this=this;var eventId=hint&&hint.event_id;this._processing=true;this._processEvent(event,hint,scope).then(function(finalEvent){// We need to check for finalEvent in case beforeSend returned null
eventId=finalEvent&&finalEvent.event_id;_this._processing=false;}).then(null,function(reason){logger$1.error(reason);_this._processing=false;});return eventId;};/**
       * @inheritDoc
       */BaseClient.prototype.getDsn=function(){return this._dsn;};/**
       * @inheritDoc
       */BaseClient.prototype.getOptions=function(){return this._options;};/**
       * @inheritDoc
       */BaseClient.prototype.flush=function(timeout){var _this=this;return this._isClientProcessing(timeout).then(function(status){clearInterval(status.interval);return _this._getBackend().getTransport().close(timeout).then(function(transportFlushed){return status.ready&&transportFlushed;});});};/**
       * @inheritDoc
       */BaseClient.prototype.close=function(timeout){var _this=this;return this.flush(timeout).then(function(result){_this.getOptions().enabled=false;return result;});};/**
       * @inheritDoc
       */BaseClient.prototype.getIntegrations=function(){return this._integrations||{};};/**
       * @inheritDoc
       */BaseClient.prototype.getIntegration=function(integration){try{return this._integrations[integration.id]||null;}catch(_oO){logger$1.warn("Cannot retrieve integration "+integration.id+" from the current Client");return null;}};/** Waits for the client to be done with processing. */BaseClient.prototype._isClientProcessing=function(timeout){var _this=this;return new SyncPromise(function(resolve){var ticked=0;var tick=1;var interval=0;clearInterval(interval);interval=setInterval(function(){if(!_this._processing){resolve({interval:interval,ready:true});}else{ticked+=tick;if(timeout&&ticked>=timeout){resolve({interval:interval,ready:false});}}},tick);});};/** Returns the current backend. */BaseClient.prototype._getBackend=function(){return this._backend;};/** Determines whether this SDK is enabled and a valid Dsn is present. */BaseClient.prototype._isEnabled=function(){return this.getOptions().enabled!==false&&this._dsn!==undefined;};/**
       * Adds common information to events.
       *
       * The information includes release and environment from `options`,
       * breadcrumbs and context (extra, tags and user) from the scope.
       *
       * Information that is already present in the event is never overwritten. For
       * nested objects, such as the context, keys are merged.
       *
       * @param event The original event.
       * @param hint May contain additional informartion about the original exception.
       * @param scope A scope containing event metadata.
       * @returns A new event with more information.
       */BaseClient.prototype._prepareEvent=function(event,scope,hint){var _a=this.getOptions(),environment=_a.environment,release=_a.release,dist=_a.dist,_b=_a.maxValueLength,maxValueLength=_b===void 0?250:_b;var prepared=_assign({},event);if(prepared.environment===undefined&&environment!==undefined){prepared.environment=environment;}if(prepared.release===undefined&&release!==undefined){prepared.release=release;}if(prepared.dist===undefined&&dist!==undefined){prepared.dist=dist;}if(prepared.message){prepared.message=truncate(prepared.message,maxValueLength);}var exception=prepared.exception&&prepared.exception.values&&prepared.exception.values[0];if(exception&&exception.value){exception.value=truncate(exception.value,maxValueLength);}var request=prepared.request;if(request&&request.url){request.url=truncate(request.url,maxValueLength);}if(prepared.event_id===undefined){prepared.event_id=uuid4();}this._addIntegrations(prepared.sdk);// We prepare the result here with a resolved Event.
var result=SyncPromise.resolve(prepared);// This should be the last thing called, since we want that
// {@link Hub.addEventProcessor} gets the finished prepared event.
if(scope){// In case we have a hub we reassign it.
result=scope.applyToEvent(prepared,hint);}return result;};/**
       * This function adds all used integrations to the SDK info in the event.
       * @param sdkInfo The sdkInfo of the event that will be filled with all integrations.
       */BaseClient.prototype._addIntegrations=function(sdkInfo){var integrationsArray=Object.keys(this._integrations);if(sdkInfo&&integrationsArray.length>0){sdkInfo.integrations=integrationsArray;}};/**
       * Processes an event (either error or message) and sends it to Sentry.
       *
       * This also adds breadcrumbs and context information to the event. However,
       * platform specific meta data (such as the User's IP address) must be added
       * by the SDK implementor.
       *
       *
       * @param event The event to send to Sentry.
       * @param hint May contain additional informartion about the original exception.
       * @param scope A scope containing event metadata.
       * @returns A SyncPromise that resolves with the event or rejects in case event was/will not be send.
       */BaseClient.prototype._processEvent=function(event,hint,scope){var _this=this;var _a=this.getOptions(),beforeSend=_a.beforeSend,sampleRate=_a.sampleRate;if(!this._isEnabled()){return SyncPromise.reject('SDK not enabled, will not send event.');}// 1.0 === 100% events are sent
// 0.0 === 0% events are sent
if(typeof sampleRate==='number'&&Math.random()>sampleRate){return SyncPromise.reject('This event has been sampled, will not send event.');}return new SyncPromise(function(resolve,reject){_this._prepareEvent(event,scope,hint).then(function(prepared){if(prepared===null){reject('An event processor returned null, will not send event.');return;}var finalEvent=prepared;try{var isInternalException=hint&&hint.data&&hint.data.__sentry__===true;if(isInternalException||!beforeSend){_this._getBackend().sendEvent(finalEvent);resolve(finalEvent);return;}var beforeSendResult=beforeSend(prepared,hint);// tslint:disable-next-line:strict-type-predicates
if(typeof beforeSendResult==='undefined'){logger$1.error('`beforeSend` method has to return `null` or a valid event.');}else if(isThenable(beforeSendResult)){_this._handleAsyncBeforeSend(beforeSendResult,resolve,reject);}else{finalEvent=beforeSendResult;if(finalEvent===null){logger$1.log('`beforeSend` returned `null`, will not send event.');resolve(null);return;}// From here on we are really async
_this._getBackend().sendEvent(finalEvent);resolve(finalEvent);}}catch(exception){_this.captureException(exception,{data:{__sentry__:true},originalException:exception});reject('`beforeSend` threw an error, will not send event.');}}).then(null,function(){reject('`beforeSend` threw an error, will not send event.');});});};/**
       * Resolves before send Promise and calls resolve/reject on parent SyncPromise.
       */BaseClient.prototype._handleAsyncBeforeSend=function(beforeSend,resolve,reject){var _this=this;beforeSend.then(function(processedEvent){if(processedEvent===null){reject('`beforeSend` returned `null`, will not send event.');return;}// From here on we are really async
_this._getBackend().sendEvent(processedEvent);resolve(processedEvent);}).then(null,function(e){reject("beforeSend rejected with "+e);});};return BaseClient;}();/** Noop transport */var NoopTransport=/** @class */function(){function NoopTransport(){}/**
       * @inheritDoc
       */NoopTransport.prototype.sendEvent=function(_){return SyncPromise.resolve({reason:"NoopTransport: Event has been skipped because no Dsn is configured.",status:Status.Skipped});};/**
       * @inheritDoc
       */NoopTransport.prototype.close=function(_){return SyncPromise.resolve(true);};return NoopTransport;}();/**
   * This is the base implemention of a Backend.
   * @hidden
   */var BaseBackend=/** @class */function(){/** Creates a new backend instance. */function BaseBackend(options){this._options=options;if(!this._options.dsn){logger$1.warn('No DSN provided, backend will not do anything.');}this._transport=this._setupTransport();}/**
       * Sets up the transport so it can be used later to send requests.
       */BaseBackend.prototype._setupTransport=function(){return new NoopTransport();};/**
       * @inheritDoc
       */BaseBackend.prototype.eventFromException=function(_exception,_hint){throw new SentryError('Backend has to implement `eventFromException` method');};/**
       * @inheritDoc
       */BaseBackend.prototype.eventFromMessage=function(_message,_level,_hint){throw new SentryError('Backend has to implement `eventFromMessage` method');};/**
       * @inheritDoc
       */BaseBackend.prototype.sendEvent=function(event){this._transport.sendEvent(event).then(null,function(reason){logger$1.error("Error while sending event: "+reason);});};/**
       * @inheritDoc
       */BaseBackend.prototype.getTransport=function(){return this._transport;};return BaseBackend;}();/**
   * Internal function to create a new SDK client instance. The client is
   * installed and then bound to the current scope.
   *
   * @param clientClass The client class to instanciate.
   * @param options Options to pass to the client.
   */function initAndBind(clientClass,options){if(options.debug===true){logger$1.enable();}getCurrentHub().bindClient(new clientClass(options));}var originalFunctionToString;/** Patch toString calls to return proper name for wrapped functions */var FunctionToString=/** @class */function(){function FunctionToString(){/**
           * @inheritDoc
           */this.name=FunctionToString.id;}/**
       * @inheritDoc
       */FunctionToString.prototype.setupOnce=function(){originalFunctionToString=Function.prototype.toString;Function.prototype.toString=function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}var context=this.__sentry__?this.__sentry_original__:this;// tslint:disable-next-line:no-unsafe-any
return originalFunctionToString.apply(context,args);};};/**
       * @inheritDoc
       */FunctionToString.id='FunctionToString';return FunctionToString;}();// "Script error." is hard coded into browsers for errors that it can't read.
// this is the result of a script being pulled in from an external domain and CORS.
var DEFAULT_IGNORE_ERRORS=[/^Script error\.?$/,/^Javascript error: Script error\.? on line 0$/];/** Inbound filters configurable by the user */var InboundFilters=/** @class */function(){function InboundFilters(_options){if(_options===void 0){_options={};}this._options=_options;/**
           * @inheritDoc
           */this.name=InboundFilters.id;}/**
       * @inheritDoc
       */InboundFilters.prototype.setupOnce=function(){addGlobalEventProcessor(function(event){var hub=getCurrentHub();if(!hub){return event;}var self=hub.getIntegration(InboundFilters);if(self){var client=hub.getClient();var clientOptions=client?client.getOptions():{};var options=self._mergeOptions(clientOptions);if(self._shouldDropEvent(event,options)){return null;}}return event;});};/** JSDoc */InboundFilters.prototype._shouldDropEvent=function(event,options){if(this._isSentryError(event,options)){logger$1.warn("Event dropped due to being internal Sentry Error.\nEvent: "+getEventDescription(event));return true;}if(this._isIgnoredError(event,options)){logger$1.warn("Event dropped due to being matched by `ignoreErrors` option.\nEvent: "+getEventDescription(event));return true;}if(this._isBlacklistedUrl(event,options)){logger$1.warn("Event dropped due to being matched by `blacklistUrls` option.\nEvent: "+getEventDescription(event)+".\nUrl: "+this._getEventFilterUrl(event));return true;}if(!this._isWhitelistedUrl(event,options)){logger$1.warn("Event dropped due to not being matched by `whitelistUrls` option.\nEvent: "+getEventDescription(event)+".\nUrl: "+this._getEventFilterUrl(event));return true;}return false;};/** JSDoc */InboundFilters.prototype._isSentryError=function(event,options){if(options===void 0){options={};}if(!options.ignoreInternal){return false;}try{return event&&event.exception&&event.exception.values&&event.exception.values[0]&&event.exception.values[0].type==='SentryError'||false;}catch(_oO){return false;}};/** JSDoc */InboundFilters.prototype._isIgnoredError=function(event,options){if(options===void 0){options={};}if(!options.ignoreErrors||!options.ignoreErrors.length){return false;}return this._getPossibleEventMessages(event).some(function(message){// Not sure why TypeScript complains here...
return options.ignoreErrors.some(function(pattern){return isMatchingPattern(message,pattern);});});};/** JSDoc */InboundFilters.prototype._isBlacklistedUrl=function(event,options){if(options===void 0){options={};}// TODO: Use Glob instead?
if(!options.blacklistUrls||!options.blacklistUrls.length){return false;}var url=this._getEventFilterUrl(event);return!url?false:options.blacklistUrls.some(function(pattern){return isMatchingPattern(url,pattern);});};/** JSDoc */InboundFilters.prototype._isWhitelistedUrl=function(event,options){if(options===void 0){options={};}// TODO: Use Glob instead?
if(!options.whitelistUrls||!options.whitelistUrls.length){return true;}var url=this._getEventFilterUrl(event);return!url?true:options.whitelistUrls.some(function(pattern){return isMatchingPattern(url,pattern);});};/** JSDoc */InboundFilters.prototype._mergeOptions=function(clientOptions){if(clientOptions===void 0){clientOptions={};}return{blacklistUrls:__spread(this._options.blacklistUrls||[],clientOptions.blacklistUrls||[]),ignoreErrors:__spread(this._options.ignoreErrors||[],clientOptions.ignoreErrors||[],DEFAULT_IGNORE_ERRORS),ignoreInternal:typeof this._options.ignoreInternal!=='undefined'?this._options.ignoreInternal:true,whitelistUrls:__spread(this._options.whitelistUrls||[],clientOptions.whitelistUrls||[])};};/** JSDoc */InboundFilters.prototype._getPossibleEventMessages=function(event){if(event.message){return[event.message];}if(event.exception){try{var _a=event.exception.values&&event.exception.values[0]||{},_b=_a.type,type=_b===void 0?'':_b,_c=_a.value,value=_c===void 0?'':_c;return[""+value,type+": "+value];}catch(oO){logger$1.error("Cannot extract message for event "+getEventDescription(event));return[];}}return[];};/** JSDoc */InboundFilters.prototype._getEventFilterUrl=function(event){try{if(event.stacktrace){var frames_1=event.stacktrace.frames;return frames_1&&frames_1[frames_1.length-1].filename||null;}if(event.exception){var frames_2=event.exception.values&&event.exception.values[0].stacktrace&&event.exception.values[0].stacktrace.frames;return frames_2&&frames_2[frames_2.length-1].filename||null;}return null;}catch(oO){logger$1.error("Cannot extract url for event "+getEventDescription(event));return null;}};/**
       * @inheritDoc
       */InboundFilters.id='InboundFilters';return InboundFilters;}();var CoreIntegrations=/*#__PURE__*/Object.freeze({__proto__:null,FunctionToString:FunctionToString,InboundFilters:InboundFilters});// tslint:disable:object-literal-sort-keys
// global reference to slice
var UNKNOWN_FUNCTION='?';// Chromium based browsers: Chrome, Brave, new Opera, new Edge
var chrome=/^\s*at (?:(.*?) ?\()?((?:file|https?|blob|chrome-extension|native|eval|webpack|<anonymous>|[-a-z]+:|\/).*?)(?::(\d+))?(?::(\d+))?\)?\s*$/i;// gecko regex: `(?:bundle|\d+\.js)`: `bundle` is for react native, `\d+\.js` also but specifically for ram bundles because it
// generates filenames without a prefix like `file://` the filenames in the stacktrace are just 42.js
// We need this specific case for now because we want no other regex to match.
var gecko=/^\s*(.*?)(?:\((.*?)\))?(?:^|@)?((?:file|https?|blob|chrome|webpack|resource|moz-extension).*?:\/.*?|\[native code\]|[^@]*(?:bundle|\d+\.js))(?::(\d+))?(?::(\d+))?\s*$/i;var winjs=/^\s*at (?:((?:\[object object\])?.+) )?\(?((?:file|ms-appx|https?|webpack|blob):.*?):(\d+)(?::(\d+))?\)?\s*$/i;var geckoEval=/(\S+) line (\d+)(?: > eval line \d+)* > eval/i;var chromeEval=/\((\S*)(?::(\d+))(?::(\d+))\)/;/** JSDoc */function computeStackTrace(ex){// tslint:disable:no-unsafe-any
var stack=null;var popSize=ex&&ex.framesToPop;try{// This must be tried first because Opera 10 *destroys*
// its stacktrace property if you try to access the stack
// property first!!
stack=computeStackTraceFromStacktraceProp(ex);if(stack){return popFrames(stack,popSize);}}catch(e){// no-empty
}try{stack=computeStackTraceFromStackProp(ex);if(stack){return popFrames(stack,popSize);}}catch(e){// no-empty
}return{message:extractMessage(ex),name:ex&&ex.name,stack:[],failed:true};}/** JSDoc */ // tslint:disable-next-line:cyclomatic-complexity
function computeStackTraceFromStackProp(ex){// tslint:disable:no-conditional-assignment
if(!ex||!ex.stack){return null;}var stack=[];var lines=ex.stack.split('\n');var isEval;var submatch;var parts;var element;for(var i=0;i<lines.length;++i){if(parts=chrome.exec(lines[i])){var isNative=parts[2]&&parts[2].indexOf('native')===0;// start of line
isEval=parts[2]&&parts[2].indexOf('eval')===0;// start of line
if(isEval&&(submatch=chromeEval.exec(parts[2]))){// throw out eval line/column and use top-most line/column number
parts[2]=submatch[1];// url
parts[3]=submatch[2];// line
parts[4]=submatch[3];// column
}element={url:parts[2],func:parts[1]||UNKNOWN_FUNCTION,args:isNative?[parts[2]]:[],line:parts[3]?+parts[3]:null,column:parts[4]?+parts[4]:null};}else if(parts=winjs.exec(lines[i])){element={url:parts[2],func:parts[1]||UNKNOWN_FUNCTION,args:[],line:+parts[3],column:parts[4]?+parts[4]:null};}else if(parts=gecko.exec(lines[i])){isEval=parts[3]&&parts[3].indexOf(' > eval')>-1;if(isEval&&(submatch=geckoEval.exec(parts[3]))){// throw out eval line/column and use top-most line number
parts[1]=parts[1]||"eval";parts[3]=submatch[1];parts[4]=submatch[2];parts[5]='';// no column when eval
}else if(i===0&&!parts[5]&&ex.columnNumber!==void 0){// FireFox uses this awesome columnNumber property for its top frame
// Also note, Firefox's column number is 0-based and everything else expects 1-based,
// so adding 1
// NOTE: this hack doesn't work if top-most frame is eval
stack[0].column=ex.columnNumber+1;}element={url:parts[3],func:parts[1]||UNKNOWN_FUNCTION,args:parts[2]?parts[2].split(','):[],line:parts[4]?+parts[4]:null,column:parts[5]?+parts[5]:null};}else{continue;}if(!element.func&&element.line){element.func=UNKNOWN_FUNCTION;}stack.push(element);}if(!stack.length){return null;}return{message:extractMessage(ex),name:ex.name,stack:stack};}/** JSDoc */function computeStackTraceFromStacktraceProp(ex){if(!ex||!ex.stacktrace){return null;}// Access and store the stacktrace property before doing ANYTHING
// else to it because Opera is not very good at providing it
// reliably in other circumstances.
var stacktrace=ex.stacktrace;var opera10Regex=/ line (\d+).*script (?:in )?(\S+)(?:: in function (\S+))?$/i;var opera11Regex=/ line (\d+), column (\d+)\s*(?:in (?:<anonymous function: ([^>]+)>|([^\)]+))\((.*)\))? in (.*):\s*$/i;var lines=stacktrace.split('\n');var stack=[];var parts;for(var line=0;line<lines.length;line+=2){// tslint:disable:no-conditional-assignment
var element=null;if(parts=opera10Regex.exec(lines[line])){element={url:parts[2],func:parts[3],args:[],line:+parts[1],column:null};}else if(parts=opera11Regex.exec(lines[line])){element={url:parts[6],func:parts[3]||parts[4],args:parts[5]?parts[5].split(','):[],line:+parts[1],column:+parts[2]};}if(element){if(!element.func&&element.line){element.func=UNKNOWN_FUNCTION;}stack.push(element);}}if(!stack.length){return null;}return{message:extractMessage(ex),name:ex.name,stack:stack};}/** Remove N number of frames from the stack */function popFrames(stacktrace,popSize){try{return _assign({},stacktrace,{stack:stacktrace.stack.slice(popSize)});}catch(e){return stacktrace;}}/**
   * There are cases where stacktrace.message is an Event object
   * https://github.com/getsentry/sentry-javascript/issues/1949
   * In this specific case we try to extract stacktrace.message.error.message
   */function extractMessage(ex){var message=ex&&ex.message;if(!message){return'No error message';}if(message.error&&typeof message.error.message==='string'){return message.error.message;}return message;}var STACKTRACE_LIMIT=50;/**
   * This function creates an exception from an TraceKitStackTrace
   * @param stacktrace TraceKitStackTrace that will be converted to an exception
   * @hidden
   */function exceptionFromStacktrace(stacktrace){var frames=prepareFramesForEvent(stacktrace.stack);var exception={type:stacktrace.name,value:stacktrace.message};if(frames&&frames.length){exception.stacktrace={frames:frames};}// tslint:disable-next-line:strict-type-predicates
if(exception.type===undefined&&exception.value===''){exception.value='Unrecoverable error caught';}return exception;}/**
   * @hidden
   */function eventFromPlainObject(exception,syntheticException,rejection){var event={exception:{values:[{type:isEvent(exception)?exception.constructor.name:rejection?'UnhandledRejection':'Error',value:"Non-Error "+(rejection?'promise rejection':'exception')+" captured with keys: "+extractExceptionKeysForMessage(exception)}]},extra:{__serialized__:normalizeToSize(exception)}};if(syntheticException){var stacktrace=computeStackTrace(syntheticException);var frames_1=prepareFramesForEvent(stacktrace.stack);event.stacktrace={frames:frames_1};}return event;}/**
   * @hidden
   */function eventFromStacktrace(stacktrace){var exception=exceptionFromStacktrace(stacktrace);return{exception:{values:[exception]}};}/**
   * @hidden
   */function prepareFramesForEvent(stack){if(!stack||!stack.length){return[];}var localStack=stack;var firstFrameFunction=localStack[0].func||'';var lastFrameFunction=localStack[localStack.length-1].func||'';// If stack starts with one of our API calls, remove it (starts, meaning it's the top of the stack - aka last call)
if(firstFrameFunction.indexOf('captureMessage')!==-1||firstFrameFunction.indexOf('captureException')!==-1){localStack=localStack.slice(1);}// If stack ends with one of our internal API calls, remove it (ends, meaning it's the bottom of the stack - aka top-most call)
if(lastFrameFunction.indexOf('sentryWrapped')!==-1){localStack=localStack.slice(0,-1);}// The frame where the crash happened, should be the last entry in the array
return localStack.map(function(frame){return{colno:frame.column===null?undefined:frame.column,filename:frame.url||localStack[0].url,"function":frame.func||'?',in_app:true,lineno:frame.line===null?undefined:frame.line};}).slice(0,STACKTRACE_LIMIT).reverse();}/** JSDoc */function eventFromUnknownInput(exception,syntheticException,options){if(options===void 0){options={};}var event;if(isErrorEvent(exception)&&exception.error){// If it is an ErrorEvent with `error` property, extract it to get actual Error
var errorEvent=exception;exception=errorEvent.error;// tslint:disable-line:no-parameter-reassignment
event=eventFromStacktrace(computeStackTrace(exception));return event;}if(isDOMError(exception)||isDOMException(exception)){// If it is a DOMError or DOMException (which are legacy APIs, but still supported in some browsers)
// then we just extract the name and message, as they don't provide anything else
// https://developer.mozilla.org/en-US/docs/Web/API/DOMError
// https://developer.mozilla.org/en-US/docs/Web/API/DOMException
var domException=exception;var name_1=domException.name||(isDOMError(domException)?'DOMError':'DOMException');var message=domException.message?name_1+": "+domException.message:name_1;event=eventFromString(message,syntheticException,options);addExceptionTypeValue(event,message);return event;}if(isError(exception)){// we have a real Error object, do nothing
event=eventFromStacktrace(computeStackTrace(exception));return event;}if(isPlainObject$1(exception)||isEvent(exception)){// If it is plain Object or Event, serialize it manually and extract options
// This will allow us to group events based on top-level keys
// which is much better than creating new group when any key/value change
var objectException=exception;event=eventFromPlainObject(objectException,syntheticException,options.rejection);addExceptionMechanism(event,{synthetic:true});return event;}// If none of previous checks were valid, then it means that it's not:
// - an instance of DOMError
// - an instance of DOMException
// - an instance of Event
// - an instance of Error
// - a valid ErrorEvent (one with an error property)
// - a plain Object
//
// So bail out and capture it as a simple message:
event=eventFromString(exception,syntheticException,options);addExceptionTypeValue(event,""+exception,undefined);addExceptionMechanism(event,{synthetic:true});return event;}// this._options.attachStacktrace
/** JSDoc */function eventFromString(input,syntheticException,options){if(options===void 0){options={};}var event={message:input};if(options.attachStacktrace&&syntheticException){var stacktrace=computeStackTrace(syntheticException);var frames_1=prepareFramesForEvent(stacktrace.stack);event.stacktrace={frames:frames_1};}return event;}/** Base Transport class implementation */var BaseTransport=/** @class */function(){function BaseTransport(options){this.options=options;/** A simple buffer holding all requests. */this._buffer=new PromiseBuffer(30);this.url=new API(this.options.dsn).getStoreEndpointWithUrlEncodedAuth();}/**
       * @inheritDoc
       */BaseTransport.prototype.sendEvent=function(_){throw new SentryError('Transport Class has to implement `sendEvent` method');};/**
       * @inheritDoc
       */BaseTransport.prototype.close=function(timeout){return this._buffer.drain(timeout);};return BaseTransport;}();var global$2=getGlobalObject();/** `fetch` based transport */var FetchTransport=/** @class */function(_super){__extends(FetchTransport,_super);function FetchTransport(){var _this=_super!==null&&_super.apply(this,arguments)||this;/** Locks transport after receiving 429 response */_this._disabledUntil=new Date(Date.now());return _this;}/**
       * @inheritDoc
       */FetchTransport.prototype.sendEvent=function(event){var _this=this;if(new Date(Date.now())<this._disabledUntil){return Promise.reject({event:event,reason:"Transport locked till "+this._disabledUntil+" due to too many requests.",status:429});}var defaultOptions={body:JSON.stringify(event),method:'POST',// Despite all stars in the sky saying that Edge supports old draft syntax, aka 'never', 'always', 'origin' and 'default
// https://caniuse.com/#feat=referrer-policy
// It doesn't. And it throw exception instead of ignoring this parameter...
// REF: https://github.com/getsentry/raven-js/issues/1233
referrerPolicy:supportsReferrerPolicy()?'origin':''};return this._buffer.add(new SyncPromise(function(resolve,reject){return __awaiter(_this,void 0,void 0,function(){var response,err_1,status,now;return __generator(this,function(_a){switch(_a.label){case 0:_a.trys.push([0,2,,3]);return[4/*yield*/,global$2.fetch(this.url,defaultOptions)];case 1:response=_a.sent();return[3/*break*/,3];case 2:err_1=_a.sent();reject(err_1);return[2/*return*/];case 3:status=Status.fromHttpCode(response.status);if(status===Status.Success){resolve({status:status});return[2/*return*/];}if(status===Status.RateLimit){now=Date.now();this._disabledUntil=new Date(now+parseRetryAfterHeader(now,response.headers.get('Retry-After')));logger$1.warn("Too many requests, backing off till: "+this._disabledUntil);}reject(response);return[2/*return*/];}});});}));};return FetchTransport;}(BaseTransport);/** `XHR` based transport */var XHRTransport=/** @class */function(_super){__extends(XHRTransport,_super);function XHRTransport(){var _this=_super!==null&&_super.apply(this,arguments)||this;/** Locks transport after receiving 429 response */_this._disabledUntil=new Date(Date.now());return _this;}/**
       * @inheritDoc
       */XHRTransport.prototype.sendEvent=function(event){var _this=this;if(new Date(Date.now())<this._disabledUntil){return Promise.reject({event:event,reason:"Transport locked till "+this._disabledUntil+" due to too many requests.",status:429});}return this._buffer.add(new SyncPromise(function(resolve,reject){var request=new XMLHttpRequest();request.onreadystatechange=function(){if(request.readyState!==4){return;}var status=Status.fromHttpCode(request.status);if(status===Status.Success){resolve({status:status});return;}if(status===Status.RateLimit){var now=Date.now();_this._disabledUntil=new Date(now+parseRetryAfterHeader(now,request.getResponseHeader('Retry-After')));logger$1.warn("Too many requests, backing off till: "+_this._disabledUntil);}reject(request);};request.open('POST',_this.url);request.send(JSON.stringify(event));}));};return XHRTransport;}(BaseTransport);var index$1=/*#__PURE__*/Object.freeze({__proto__:null,BaseTransport:BaseTransport,FetchTransport:FetchTransport,XHRTransport:XHRTransport});/**
   * The Sentry Browser SDK Backend.
   * @hidden
   */var BrowserBackend=/** @class */function(_super){__extends(BrowserBackend,_super);function BrowserBackend(){return _super!==null&&_super.apply(this,arguments)||this;}/**
       * @inheritDoc
       */BrowserBackend.prototype._setupTransport=function(){if(!this._options.dsn){// We return the noop transport here in case there is no Dsn.
return _super.prototype._setupTransport.call(this);}var transportOptions=_assign({},this._options.transportOptions,{dsn:this._options.dsn});if(this._options.transport){return new this._options.transport(transportOptions);}if(supportsFetch()){return new FetchTransport(transportOptions);}return new XHRTransport(transportOptions);};/**
       * @inheritDoc
       */BrowserBackend.prototype.eventFromException=function(exception,hint){var syntheticException=hint&&hint.syntheticException||undefined;var event=eventFromUnknownInput(exception,syntheticException,{attachStacktrace:this._options.attachStacktrace});addExceptionMechanism(event,{handled:true,type:'generic'});event.level=Severity.Error;if(hint&&hint.event_id){event.event_id=hint.event_id;}return SyncPromise.resolve(event);};/**
       * @inheritDoc
       */BrowserBackend.prototype.eventFromMessage=function(message,level,hint){if(level===void 0){level=Severity.Info;}var syntheticException=hint&&hint.syntheticException||undefined;var event=eventFromString(message,syntheticException,{attachStacktrace:this._options.attachStacktrace});event.level=level;if(hint&&hint.event_id){event.event_id=hint.event_id;}return SyncPromise.resolve(event);};return BrowserBackend;}(BaseBackend);var SDK_NAME='sentry.javascript.browser';var SDK_VERSION='5.8.0';/**
   * The Sentry Browser SDK Client.
   *
   * @see BrowserOptions for documentation on configuration options.
   * @see SentryClient for usage documentation.
   */var BrowserClient=/** @class */function(_super){__extends(BrowserClient,_super);/**
       * Creates a new Browser SDK instance.
       *
       * @param options Configuration options for this SDK.
       */function BrowserClient(options){if(options===void 0){options={};}return _super.call(this,BrowserBackend,options)||this;}/**
       * @inheritDoc
       */BrowserClient.prototype._prepareEvent=function(event,scope,hint){event.platform=event.platform||'javascript';event.sdk=_assign({},event.sdk,{name:SDK_NAME,packages:__spread(event.sdk&&event.sdk.packages||[],[{name:'npm:@sentry/browser',version:SDK_VERSION}]),version:SDK_VERSION});return _super.prototype._prepareEvent.call(this,event,scope,hint);};/**
       * Show a report dialog to the user to send feedback to a specific event.
       *
       * @param options Set individual options for the dialog
       */BrowserClient.prototype.showReportDialog=function(options){if(options===void 0){options={};}// doesn't work without a document (React Native)
var document=getGlobalObject().document;if(!document){return;}if(!this._isEnabled()){logger$1.error('Trying to call showReportDialog with Sentry Client is disabled');return;}var dsn=options.dsn||this.getDsn();if(!options.eventId){logger$1.error('Missing `eventId` option in showReportDialog call');return;}if(!dsn){logger$1.error('Missing `Dsn` option in showReportDialog call');return;}var script=document.createElement('script');script.async=true;script.src=new API(dsn).getReportDialogEndpoint(options);if(options.onLoad){script.onload=options.onLoad;}(document.head||document.body).appendChild(script);};return BrowserClient;}(BaseClient);var debounceDuration=1000;var keypressTimeout;var lastCapturedEvent;var ignoreOnError=0;/**
   * @hidden
   */function shouldIgnoreOnError(){return ignoreOnError>0;}/**
   * @hidden
   */function ignoreNextOnError(){// onerror should trigger before setTimeout
ignoreOnError+=1;setTimeout(function(){ignoreOnError-=1;});}/**
   * Instruments the given function and sends an event to Sentry every time the
   * function throws an exception.
   *
   * @param fn A function to wrap.
   * @returns The wrapped function.
   * @hidden
   */function wrap(fn,options,before){if(options===void 0){options={};}// tslint:disable-next-line:strict-type-predicates
if(typeof fn!=='function'){return fn;}try{// We don't wanna wrap it twice
if(fn.__sentry__){return fn;}// If this has already been wrapped in the past, return that wrapped function
if(fn.__sentry_wrapped__){return fn.__sentry_wrapped__;}}catch(e){// Just accessing custom props in some Selenium environments
// can cause a "Permission denied" exception (see raven-js#495).
// Bail on wrapping and return the function as-is (defers to window.onerror).
return fn;}var sentryWrapped=function sentryWrapped(){// tslint:disable-next-line:strict-type-predicates
if(before&&typeof before==='function'){before.apply(this,arguments);}var args=Array.prototype.slice.call(arguments);// tslint:disable:no-unsafe-any
try{var wrappedArguments=args.map(function(arg){return wrap(arg,options);});if(fn.handleEvent){// Attempt to invoke user-land function
// NOTE: If you are a Sentry user, and you are seeing this stack frame, it
//       means the sentry.javascript SDK caught an error invoking your application code. This
//       is expected behavior and NOT indicative of a bug with sentry.javascript.
return fn.handleEvent.apply(this,wrappedArguments);}// Attempt to invoke user-land function
// NOTE: If you are a Sentry user, and you are seeing this stack frame, it
//       means the sentry.javascript SDK caught an error invoking your application code. This
//       is expected behavior and NOT indicative of a bug with sentry.javascript.
return fn.apply(this,wrappedArguments);// tslint:enable:no-unsafe-any
}catch(ex){ignoreNextOnError();withScope(function(scope){scope.addEventProcessor(function(event){var processedEvent=_assign({},event);if(options.mechanism){addExceptionTypeValue(processedEvent,undefined,undefined);addExceptionMechanism(processedEvent,options.mechanism);}processedEvent.extra=_assign({},processedEvent.extra,{arguments:normalize$1(args,3)});return processedEvent;});captureException(ex);});throw ex;}};// Accessing some objects may throw
// ref: https://github.com/getsentry/sentry-javascript/issues/1168
try{for(var property in fn){if(Object.prototype.hasOwnProperty.call(fn,property)){sentryWrapped[property]=fn[property];}}}catch(_oO){}// tslint:disable-line:no-empty
fn.prototype=fn.prototype||{};sentryWrapped.prototype=fn.prototype;Object.defineProperty(fn,'__sentry_wrapped__',{enumerable:false,value:sentryWrapped});// Signal that this function has been wrapped/filled already
// for both debugging and to prevent it to being wrapped/filled twice
Object.defineProperties(sentryWrapped,{__sentry__:{enumerable:false,value:true},__sentry_original__:{enumerable:false,value:fn}});// Restore original function name (not all browsers allow that)
try{var descriptor=Object.getOwnPropertyDescriptor(sentryWrapped,'name');if(descriptor.configurable){Object.defineProperty(sentryWrapped,'name',{get:function get(){return fn.name;}});}}catch(_oO){/*no-empty*/}return sentryWrapped;}var debounceTimer=0;/**
   * Wraps addEventListener to capture UI breadcrumbs
   * @param eventName the event name (e.g. "click")
   * @returns wrapped breadcrumb events handler
   * @hidden
   */function breadcrumbEventHandler(eventName,debounce){if(debounce===void 0){debounce=false;}return function(event){// reset keypress timeout; e.g. triggering a 'click' after
// a 'keypress' will reset the keypress debounce so that a new
// set of keypresses can be recorded
keypressTimeout=undefined;// It's possible this handler might trigger multiple times for the same
// event (e.g. event propagation through node ancestors). Ignore if we've
// already captured the event.
if(!event||lastCapturedEvent===event){return;}lastCapturedEvent=event;var captureBreadcrumb=function captureBreadcrumb(){var target;// Accessing event.target can throw (see getsentry/raven-js#838, #768)
try{target=event.target?htmlTreeAsString(event.target):htmlTreeAsString(event);}catch(e){target='<unknown>';}if(target.length===0){return;}getCurrentHub().addBreadcrumb({category:"ui."+eventName,message:target},{event:event,name:eventName});};if(debounceTimer){clearTimeout(debounceTimer);}if(debounce){debounceTimer=setTimeout(captureBreadcrumb);}else{captureBreadcrumb();}};}/**
   * Wraps addEventListener to capture keypress UI events
   * @returns wrapped keypress events handler
   * @hidden
   */function keypressEventHandler(){// TODO: if somehow user switches keypress target before
//       debounce timeout is triggered, we will only capture
//       a single breadcrumb from the FIRST target (acceptable?)
return function(event){var target;try{target=event.target;}catch(e){// just accessing event properties can throw an exception in some rare circumstances
// see: https://github.com/getsentry/raven-js/issues/838
return;}var tagName=target&&target.tagName;// only consider keypress events on actual input elements
// this will disregard keypresses targeting body (e.g. tabbing
// through elements, hotkeys, etc)
if(!tagName||tagName!=='INPUT'&&tagName!=='TEXTAREA'&&!target.isContentEditable){return;}// record first keypress in a series, but ignore subsequent
// keypresses until debounce clears
if(!keypressTimeout){breadcrumbEventHandler('input')(event);}clearTimeout(keypressTimeout);keypressTimeout=setTimeout(function(){keypressTimeout=undefined;},debounceDuration);};}/** Global handlers */var GlobalHandlers=/** @class */function(){/** JSDoc */function GlobalHandlers(options){/**
           * @inheritDoc
           */this.name=GlobalHandlers.id;/** JSDoc */this._global=getGlobalObject();/** JSDoc */this._oldOnErrorHandler=null;/** JSDoc */this._oldOnUnhandledRejectionHandler=null;/** JSDoc */this._onErrorHandlerInstalled=false;/** JSDoc */this._onUnhandledRejectionHandlerInstalled=false;this._options=_assign({onerror:true,onunhandledrejection:true},options);}/**
       * @inheritDoc
       */GlobalHandlers.prototype.setupOnce=function(){Error.stackTraceLimit=50;if(this._options.onerror){logger$1.log('Global Handler attached: onerror');this._installGlobalOnErrorHandler();}if(this._options.onunhandledrejection){logger$1.log('Global Handler attached: onunhandledrejection');this._installGlobalOnUnhandledRejectionHandler();}};/** JSDoc */GlobalHandlers.prototype._installGlobalOnErrorHandler=function(){if(this._onErrorHandlerInstalled){return;}var self=this;// tslint:disable-line:no-this-assignment
this._oldOnErrorHandler=this._global.onerror;this._global.onerror=function(msg,url,line,column,error){var currentHub=getCurrentHub();var hasIntegration=currentHub.getIntegration(GlobalHandlers);var isFailedOwnDelivery=error&&error.__sentry_own_request__===true;if(!hasIntegration||shouldIgnoreOnError()||isFailedOwnDelivery){if(self._oldOnErrorHandler){return self._oldOnErrorHandler.apply(this,arguments);}return false;}var client=currentHub.getClient();var event=isPrimitive$1(error)?self._eventFromIncompleteOnError(msg,url,line,column):self._enhanceEventWithInitialFrame(eventFromUnknownInput(error,undefined,{attachStacktrace:client&&client.getOptions().attachStacktrace,rejection:false}),url,line,column);addExceptionMechanism(event,{handled:false,type:'onerror'});currentHub.captureEvent(event,{originalException:error});if(self._oldOnErrorHandler){return self._oldOnErrorHandler.apply(this,arguments);}return false;};this._onErrorHandlerInstalled=true;};/** JSDoc */GlobalHandlers.prototype._installGlobalOnUnhandledRejectionHandler=function(){if(this._onUnhandledRejectionHandlerInstalled){return;}var self=this;// tslint:disable-line:no-this-assignment
this._oldOnUnhandledRejectionHandler=this._global.onunhandledrejection;this._global.onunhandledrejection=function(e){var error=e;try{error=e&&'reason'in e?e.reason:e;}catch(_oO){// no-empty
}var currentHub=getCurrentHub();var hasIntegration=currentHub.getIntegration(GlobalHandlers);var isFailedOwnDelivery=error&&error.__sentry_own_request__===true;if(!hasIntegration||shouldIgnoreOnError()||isFailedOwnDelivery){if(self._oldOnUnhandledRejectionHandler){return self._oldOnUnhandledRejectionHandler.apply(this,arguments);}return false;}var client=currentHub.getClient();var event=isPrimitive$1(error)?self._eventFromIncompleteRejection(error):eventFromUnknownInput(error,undefined,{attachStacktrace:client&&client.getOptions().attachStacktrace,rejection:true});event.level=Severity.Error;addExceptionMechanism(event,{handled:false,type:'onunhandledrejection'});currentHub.captureEvent(event,{originalException:error});if(self._oldOnUnhandledRejectionHandler){return self._oldOnUnhandledRejectionHandler.apply(this,arguments);}return false;};this._onUnhandledRejectionHandlerInstalled=true;};/**
       * This function creates a stack from an old, error-less onerror handler.
       */GlobalHandlers.prototype._eventFromIncompleteOnError=function(msg,url,line,column){var ERROR_TYPES_RE=/^(?:[Uu]ncaught (?:exception: )?)?(?:((?:Eval|Internal|Range|Reference|Syntax|Type|URI|)Error): )?(.*)$/i;// If 'message' is ErrorEvent, get real message from inside
var message=isErrorEvent(msg)?msg.message:msg;var name;if(isString(message)){var groups=message.match(ERROR_TYPES_RE);if(groups){name=groups[1];message=groups[2];}}var event={exception:{values:[{type:name||'Error',value:message}]}};return this._enhanceEventWithInitialFrame(event,url,line,column);};/**
       * This function creates an Event from an TraceKitStackTrace that has part of it missing.
       */GlobalHandlers.prototype._eventFromIncompleteRejection=function(error){return{exception:{values:[{type:'UnhandledRejection',value:"Non-Error promise rejection captured with value: "+error}]}};};/** JSDoc */GlobalHandlers.prototype._enhanceEventWithInitialFrame=function(event,url,line,column){event.exception=event.exception||{};event.exception.values=event.exception.values||[];event.exception.values[0]=event.exception.values[0]||{};event.exception.values[0].stacktrace=event.exception.values[0].stacktrace||{};event.exception.values[0].stacktrace.frames=event.exception.values[0].stacktrace.frames||[];var colno=isNaN(parseInt(column,10))?undefined:column;var lineno=isNaN(parseInt(line,10))?undefined:line;var filename=isString(url)&&url.length>0?url:getLocationHref();if(event.exception.values[0].stacktrace.frames.length===0){event.exception.values[0].stacktrace.frames.push({colno:colno,filename:filename,"function":'?',in_app:true,lineno:lineno});}return event;};/**
       * @inheritDoc
       */GlobalHandlers.id='GlobalHandlers';return GlobalHandlers;}();/** Wrap timer functions and event targets to catch errors and provide better meta data */var TryCatch=/** @class */function(){function TryCatch(){/** JSDoc */this._ignoreOnError=0;/**
           * @inheritDoc
           */this.name=TryCatch.id;}/** JSDoc */TryCatch.prototype._wrapTimeFunction=function(original){return function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}var originalCallback=args[0];args[0]=wrap(originalCallback,{mechanism:{data:{"function":getFunctionName(original)},handled:true,type:'instrument'}});return original.apply(this,args);};};/** JSDoc */TryCatch.prototype._wrapRAF=function(original){return function(callback){return original(wrap(callback,{mechanism:{data:{"function":'requestAnimationFrame',handler:getFunctionName(original)},handled:true,type:'instrument'}}));};};/** JSDoc */TryCatch.prototype._wrapEventTarget=function(target){var global=getGlobalObject();var proto=global[target]&&global[target].prototype;if(!proto||!proto.hasOwnProperty||!proto.hasOwnProperty('addEventListener')){return;}fill(proto,'addEventListener',function(original){return function(eventName,fn,options){try{// tslint:disable-next-line:no-unbound-method strict-type-predicates
if(typeof fn.handleEvent==='function'){fn.handleEvent=wrap(fn.handleEvent.bind(fn),{mechanism:{data:{"function":'handleEvent',handler:getFunctionName(fn),target:target},handled:true,type:'instrument'}});}}catch(err){// can sometimes get 'Permission denied to access property "handle Event'
}return original.call(this,eventName,wrap(fn,{mechanism:{data:{"function":'addEventListener',handler:getFunctionName(fn),target:target},handled:true,type:'instrument'}}),options);};});fill(proto,'removeEventListener',function(original){return function(eventName,fn,options){var callback=fn;try{callback=callback&&(callback.__sentry_wrapped__||callback);}catch(e){// ignore, accessing __sentry_wrapped__ will throw in some Selenium environments
}return original.call(this,eventName,callback,options);};});};/**
       * Wrap timer functions and event targets to catch errors
       * and provide better metadata.
       */TryCatch.prototype.setupOnce=function(){this._ignoreOnError=this._ignoreOnError;var global=getGlobalObject();fill(global,'setTimeout',this._wrapTimeFunction.bind(this));fill(global,'setInterval',this._wrapTimeFunction.bind(this));fill(global,'requestAnimationFrame',this._wrapRAF.bind(this));['EventTarget','Window','Node','ApplicationCache','AudioTrackList','ChannelMergerNode','CryptoOperation','EventSource','FileReader','HTMLUnknownElement','IDBDatabase','IDBRequest','IDBTransaction','KeyOperation','MediaController','MessagePort','ModalWindow','Notification','SVGElementInstance','Screen','TextTrack','TextTrackCue','TextTrackList','WebSocket','WebSocketWorker','Worker','XMLHttpRequest','XMLHttpRequestEventTarget','XMLHttpRequestUpload'].forEach(this._wrapEventTarget.bind(this));};/**
       * @inheritDoc
       */TryCatch.id='TryCatch';return TryCatch;}();/**
   * Safely extract function name from itself
   */function getFunctionName(fn){try{return fn&&fn.name||'<anonymous>';}catch(e){// Just accessing custom props in some Selenium environments
// can cause a "Permission denied" exception (see raven-js#495).
return'<anonymous>';}}var global$3=getGlobalObject();var lastHref;/** Default Breadcrumbs instrumentations */var Breadcrumbs$1=/** @class */function(){/**
       * @inheritDoc
       */function Breadcrumbs(options){/**
           * @inheritDoc
           */this.name=Breadcrumbs.id;this._options=_assign({console:true,dom:true,fetch:true,history:true,sentry:true,xhr:true},options);}/** JSDoc */Breadcrumbs.prototype._instrumentConsole=function(){if(!('console'in global$3)){return;}['debug','info','warn','error','log','assert'].forEach(function(level){if(!(level in global$3.console)){return;}fill(global$3.console,level,function(originalConsoleLevel){return function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}var breadcrumbData={category:'console',data:{extra:{arguments:normalize$1(args,3)},logger:'console'},level:Severity.fromString(level),message:safeJoin(args,' ')};if(level==='assert'){if(args[0]===false){breadcrumbData.message="Assertion failed: "+(safeJoin(args.slice(1),' ')||'console.assert');breadcrumbData.data.extra.arguments=normalize$1(args.slice(1),3);Breadcrumbs.addBreadcrumb(breadcrumbData,{input:args,level:level});}}else{Breadcrumbs.addBreadcrumb(breadcrumbData,{input:args,level:level});}// this fails for some browsers. :(
if(originalConsoleLevel){Function.prototype.apply.call(originalConsoleLevel,global$3.console,args);}};});});};/** JSDoc */Breadcrumbs.prototype._instrumentDOM=function(){if(!('document'in global$3)){return;}// Capture breadcrumbs from any click that is unhandled / bubbled up all the way
// to the document. Do this before we instrument addEventListener.
global$3.document.addEventListener('click',breadcrumbEventHandler('click'),false);global$3.document.addEventListener('keypress',keypressEventHandler(),false);// After hooking into document bubbled up click and keypresses events, we also hook into user handled click & keypresses.
['EventTarget','Node'].forEach(function(target){var proto=global$3[target]&&global$3[target].prototype;if(!proto||!proto.hasOwnProperty||!proto.hasOwnProperty('addEventListener')){return;}fill(proto,'addEventListener',function(original){return function(eventName,fn,options){if(fn&&fn.handleEvent){if(eventName==='click'){fill(fn,'handleEvent',function(innerOriginal){return function(event){breadcrumbEventHandler('click')(event);return innerOriginal.call(this,event);};});}if(eventName==='keypress'){fill(fn,'handleEvent',function(innerOriginal){return function(event){keypressEventHandler()(event);return innerOriginal.call(this,event);};});}}else{if(eventName==='click'){breadcrumbEventHandler('click',true)(this);}if(eventName==='keypress'){keypressEventHandler()(this);}}return original.call(this,eventName,fn,options);};});fill(proto,'removeEventListener',function(original){return function(eventName,fn,options){var callback=fn;try{callback=callback&&(callback.__sentry_wrapped__||callback);}catch(e){// ignore, accessing __sentry_wrapped__ will throw in some Selenium environments
}return original.call(this,eventName,callback,options);};});});};/** JSDoc */Breadcrumbs.prototype._instrumentFetch=function(){if(!supportsNativeFetch()){return;}fill(global$3,'fetch',function(originalFetch){return function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}var fetchInput=args[0];var method='GET';var url;if(typeof fetchInput==='string'){url=fetchInput;}else if('Request'in global$3&&fetchInput instanceof Request){url=fetchInput.url;if(fetchInput.method){method=fetchInput.method;}}else{url=String(fetchInput);}if(args[1]&&args[1].method){method=args[1].method;}var client=getCurrentHub().getClient();var dsn=client&&client.getDsn();if(dsn){var filterUrl=new API(dsn).getStoreEndpoint();// if Sentry key appears in URL, don't capture it as a request
// but rather as our own 'sentry' type breadcrumb
if(filterUrl&&url.indexOf(filterUrl)!==-1){if(method==='POST'&&args[1]&&args[1].body){addSentryBreadcrumb(args[1].body);}return originalFetch.apply(global$3,args);}}var fetchData={method:isString(method)?method.toUpperCase():method,url:url};return originalFetch.apply(global$3,args).then(function(response){fetchData.status_code=response.status;Breadcrumbs.addBreadcrumb({category:'fetch',data:fetchData,type:'http'},{input:args,response:response});return response;}).then(null,function(error){Breadcrumbs.addBreadcrumb({category:'fetch',data:fetchData,level:Severity.Error,type:'http'},{error:error,input:args});throw error;});};});};/** JSDoc */Breadcrumbs.prototype._instrumentHistory=function(){var _this=this;if(!supportsHistory()){return;}var captureUrlChange=function captureUrlChange(from,to){var parsedLoc=parseUrl(global$3.location.href);var parsedTo=parseUrl(to);var parsedFrom=parseUrl(from);// Initial pushState doesn't provide `from` information
if(!parsedFrom.path){parsedFrom=parsedLoc;}// because onpopstate only tells you the "new" (to) value of location.href, and
// not the previous (from) value, we need to track the value of the current URL
// state ourselves
lastHref=to;// Use only the path component of the URL if the URL matches the current
// document (almost all the time when using pushState)
if(parsedLoc.protocol===parsedTo.protocol&&parsedLoc.host===parsedTo.host){// tslint:disable-next-line:no-parameter-reassignment
to=parsedTo.relative;}if(parsedLoc.protocol===parsedFrom.protocol&&parsedLoc.host===parsedFrom.host){// tslint:disable-next-line:no-parameter-reassignment
from=parsedFrom.relative;}Breadcrumbs.addBreadcrumb({category:'navigation',data:{from:from,to:to}});};// record navigation (URL) changes
var oldOnPopState=global$3.onpopstate;global$3.onpopstate=function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}var currentHref=global$3.location.href;captureUrlChange(lastHref,currentHref);if(oldOnPopState){return oldOnPopState.apply(_this,args);}};/**
           * @hidden
           */function historyReplacementFunction(originalHistoryFunction){// note history.pushState.length is 0; intentionally not declaring
// params to preserve 0 arity
return function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}var url=args.length>2?args[2]:undefined;// url argument is optional
if(url){// coerce to string (this is what pushState does)
captureUrlChange(lastHref,String(url));}return originalHistoryFunction.apply(this,args);};}fill(global$3.history,'pushState',historyReplacementFunction);fill(global$3.history,'replaceState',historyReplacementFunction);};/** JSDoc */Breadcrumbs.prototype._instrumentXHR=function(){if(!('XMLHttpRequest'in global$3)){return;}/**
           * @hidden
           */function wrapProp(prop,xhr){if(prop in xhr&&typeof xhr[prop]==='function'){fill(xhr,prop,function(original){return wrap(original,{mechanism:{data:{"function":prop,handler:original&&original.name||'<anonymous>'},handled:true,type:'instrument'}});});}}var xhrproto=XMLHttpRequest.prototype;fill(xhrproto,'open',function(originalOpen){return function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}var url=args[1];this.__sentry_xhr__={method:isString(args[0])?args[0].toUpperCase():args[0],url:args[1]};var client=getCurrentHub().getClient();var dsn=client&&client.getDsn();if(dsn){var filterUrl=new API(dsn).getStoreEndpoint();// if Sentry key appears in URL, don't capture it as a request
// but rather as our own 'sentry' type breadcrumb
if(isString(url)&&filterUrl&&url.indexOf(filterUrl)!==-1){this.__sentry_own_request__=true;}}return originalOpen.apply(this,args);};});fill(xhrproto,'send',function(originalSend){return function(){var args=[];for(var _i=0;_i<arguments.length;_i++){args[_i]=arguments[_i];}var xhr=this;// tslint:disable-line:no-this-assignment
if(xhr.__sentry_own_request__){addSentryBreadcrumb(args[0]);}/**
                   * @hidden
                   */function onreadystatechangeHandler(){if(xhr.readyState===4){if(xhr.__sentry_own_request__){return;}try{// touching statusCode in some platforms throws
// an exception
if(xhr.__sentry_xhr__){xhr.__sentry_xhr__.status_code=xhr.status;}}catch(e){/* do nothing */}Breadcrumbs.addBreadcrumb({category:'xhr',data:xhr.__sentry_xhr__,type:'http'},{xhr:xhr});}}var xmlHttpRequestProps=['onload','onerror','onprogress'];xmlHttpRequestProps.forEach(function(prop){wrapProp(prop,xhr);});if('onreadystatechange'in xhr&&typeof xhr.onreadystatechange==='function'){fill(xhr,'onreadystatechange',function(original){return wrap(original,{mechanism:{data:{"function":'onreadystatechange',handler:original&&original.name||'<anonymous>'},handled:true,type:'instrument'}},onreadystatechangeHandler);});}else{// if onreadystatechange wasn't actually set by the page on this xhr, we
// are free to set our own and capture the breadcrumb
xhr.onreadystatechange=onreadystatechangeHandler;}return originalSend.apply(this,args);};});};/**
       * Helper that checks if integration is enabled on the client.
       * @param breadcrumb Breadcrumb
       * @param hint BreadcrumbHint
       */Breadcrumbs.addBreadcrumb=function(breadcrumb,hint){if(getCurrentHub().getIntegration(Breadcrumbs)){getCurrentHub().addBreadcrumb(breadcrumb,hint);}};/**
       * Instrument browser built-ins w/ breadcrumb capturing
       *  - Console API
       *  - DOM API (click/typing)
       *  - XMLHttpRequest API
       *  - Fetch API
       *  - History API
       */Breadcrumbs.prototype.setupOnce=function(){if(this._options.console){this._instrumentConsole();}if(this._options.dom){this._instrumentDOM();}if(this._options.xhr){this._instrumentXHR();}if(this._options.fetch){this._instrumentFetch();}if(this._options.history){this._instrumentHistory();}};/**
       * @inheritDoc
       */Breadcrumbs.id='Breadcrumbs';return Breadcrumbs;}();/** JSDoc */function addSentryBreadcrumb(serializedData){// There's always something that can go wrong with deserialization...
try{var event_1=JSON.parse(serializedData);Breadcrumbs$1.addBreadcrumb({category:'sentry',event_id:event_1.event_id,level:event_1.level||Severity.fromString('error'),message:getEventDescription(event_1)},{event:event_1});}catch(_oO){logger$1.error('Error while adding sentry type breadcrumb');}}var DEFAULT_KEY='cause';var DEFAULT_LIMIT=5;/** Adds SDK info to an event. */var LinkedErrors=/** @class */function(){/**
       * @inheritDoc
       */function LinkedErrors(options){if(options===void 0){options={};}/**
           * @inheritDoc
           */this.name=LinkedErrors.id;this._key=options.key||DEFAULT_KEY;this._limit=options.limit||DEFAULT_LIMIT;}/**
       * @inheritDoc
       */LinkedErrors.prototype.setupOnce=function(){addGlobalEventProcessor(function(event,hint){var self=getCurrentHub().getIntegration(LinkedErrors);if(self){return self._handler(event,hint);}return event;});};/**
       * @inheritDoc
       */LinkedErrors.prototype._handler=function(event,hint){if(!event.exception||!event.exception.values||!hint||!(hint.originalException instanceof Error)){return event;}var linkedErrors=this._walkErrorTree(hint.originalException,this._key);event.exception.values=__spread(linkedErrors,event.exception.values);return event;};/**
       * @inheritDoc
       */LinkedErrors.prototype._walkErrorTree=function(error,key,stack){if(stack===void 0){stack=[];}if(!(error[key]instanceof Error)||stack.length+1>=this._limit){return stack;}var stacktrace=computeStackTrace(error[key]);var exception=exceptionFromStacktrace(stacktrace);return this._walkErrorTree(error[key],key,__spread([exception],stack));};/**
       * @inheritDoc
       */LinkedErrors.id='LinkedErrors';return LinkedErrors;}();var global$4=getGlobalObject();/** UserAgent */var UserAgent=/** @class */function(){function UserAgent(){/**
           * @inheritDoc
           */this.name=UserAgent.id;}/**
       * @inheritDoc
       */UserAgent.prototype.setupOnce=function(){addGlobalEventProcessor(function(event){if(getCurrentHub().getIntegration(UserAgent)){if(!global$4.navigator||!global$4.location){return event;}// Request Interface: https://docs.sentry.io/development/sdk-dev/event-payloads/request/
var request=event.request||{};request.url=request.url||global$4.location.href;request.headers=request.headers||{};request.headers['User-Agent']=global$4.navigator.userAgent;return _assign({},event,{request:request});}return event;});};/**
       * @inheritDoc
       */UserAgent.id='UserAgent';return UserAgent;}();var BrowserIntegrations=/*#__PURE__*/Object.freeze({__proto__:null,GlobalHandlers:GlobalHandlers,TryCatch:TryCatch,Breadcrumbs:Breadcrumbs$1,LinkedErrors:LinkedErrors,UserAgent:UserAgent});var defaultIntegrations=[new InboundFilters(),new FunctionToString(),new TryCatch(),new Breadcrumbs$1(),new GlobalHandlers(),new LinkedErrors(),new UserAgent()];/**
   * The Sentry Browser SDK Client.
   *
   * To use this SDK, call the {@link init} function as early as possible when
   * loading the web page. To set context information or send manual events, use
   * the provided methods.
   *
   * @example
   *
   * ```
   *
   * import { init } from '@sentry/browser';
   *
   * init({
   *   dsn: '__DSN__',
   *   // ...
   * });
   * ```
   *
   * @example
   * ```
   *
   * import { configureScope } from '@sentry/browser';
   * configureScope((scope: Scope) => {
   *   scope.setExtra({ battery: 0.7 });
   *   scope.setTag({ user_mode: 'admin' });
   *   scope.setUser({ id: '4711' });
   * });
   * ```
   *
   * @example
   * ```
   *
   * import { addBreadcrumb } from '@sentry/browser';
   * addBreadcrumb({
   *   message: 'My Breadcrumb',
   *   // ...
   * });
   * ```
   *
   * @example
   *
   * ```
   *
   * import * as Sentry from '@sentry/browser';
   * Sentry.captureMessage('Hello, world!');
   * Sentry.captureException(new Error('Good bye'));
   * Sentry.captureEvent({
   *   message: 'Manual',
   *   stacktrace: [
   *     // ...
   *   ],
   * });
   * ```
   *
   * @see {@link BrowserOptions} for documentation on configuration options.
   */function init(options){if(options===void 0){options={};}if(options.defaultIntegrations===undefined){options.defaultIntegrations=defaultIntegrations;}if(options.release===undefined){var window_1=getGlobalObject();// This supports the variable that sentry-webpack-plugin injects
if(window_1.SENTRY_RELEASE&&window_1.SENTRY_RELEASE.id){options.release=window_1.SENTRY_RELEASE.id;}}initAndBind(BrowserClient,options);}/**
   * Present the user with a report dialog.
   *
   * @param options Everything is optional, we try to fetch all info need from the global scope.
   */function showReportDialog(options){if(options===void 0){options={};}if(!options.eventId){options.eventId=getCurrentHub().lastEventId();}var client=getCurrentHub().getClient();if(client){client.showReportDialog(options);}}/**
   * This is the getter for lastEventId.
   *
   * @returns The last event id of a captured event.
   */function lastEventId(){return getCurrentHub().lastEventId();}/**
   * This function is here to be API compatible with the loader.
   * @hidden
   */function forceLoad(){}// Noop
/**
   * This function is here to be API compatible with the loader.
   * @hidden
   */function onLoad(callback){callback();}/**
   * A promise that resolves when all current events have been sent.
   * If you provide a timeout and the queue takes longer to drain the promise returns false.
   *
   * @param timeout Maximum time in ms the client should wait.
   */function flush(timeout){var client=getCurrentHub().getClient();if(client){return client.flush(timeout);}return SyncPromise.reject(false);}/**
   * A promise that resolves when all current events have been sent.
   * If you provide a timeout and the queue takes longer to drain the promise returns false.
   *
   * @param timeout Maximum time in ms the client should wait.
   */function close(timeout){var client=getCurrentHub().getClient();if(client){return client.close(timeout);}return SyncPromise.reject(false);}/**
   * Wrap code within a try/catch block so the SDK is able to capture errors.
   *
   * @param fn A function to wrap.
   *
   * @returns The result of wrapped function call.
   */function wrap$1(fn){return wrap(fn)();// tslint:disable-line:no-unsafe-any
}var windowIntegrations={};// This block is needed to add compatibility with the integrations packages when used with a CDN
// tslint:disable: no-unsafe-any
var _window=getGlobalObject();if(_window.Sentry&&_window.Sentry.Integrations){windowIntegrations=_window.Sentry.Integrations;}// tslint:enable: no-unsafe-any
var INTEGRATIONS=_assign({},windowIntegrations,CoreIntegrations,BrowserIntegrations);var Sentry=/*#__PURE__*/Object.freeze({__proto__:null,Integrations:INTEGRATIONS,Transports:index$1,get Severity(){return Severity;},get Status(){return Status;},addGlobalEventProcessor:addGlobalEventProcessor,addBreadcrumb:addBreadcrumb,captureException:captureException,captureEvent:captureEvent,captureMessage:captureMessage,configureScope:configureScope,getHubFromCarrier:getHubFromCarrier,getCurrentHub:getCurrentHub,Hub:Hub,Scope:Scope,setContext:setContext,setExtra:setExtra,setExtras:setExtras,setTag:setTag,setTags:setTags,setUser:setUser,Span:Span,withScope:withScope,BrowserClient:BrowserClient,defaultIntegrations:defaultIntegrations,forceLoad:forceLoad,init:init,lastEventId:lastEventId,onLoad:onLoad,showReportDialog:showReportDialog,flush:flush,close:close,wrap:wrap$1,SDK_NAME:SDK_NAME,SDK_VERSION:SDK_VERSION});var script$s={name:'ErrorBoundary',data:function data(){return{sentry:null};},computed:_objectSpread({},index_esm.mapGetters(['apiClient','config'])),methods:{initSentry:function initSentry(){var integrations=this.config.useSentryBreadcrumbs?[new INTEGRATIONS.Breadcrumbs()]:[];init({dsn:ENV.sentryDSN,release:'picker@1.11.1',defaultIntegrations:false,integrations:integrations});this.sentry=Sentry;},formatComponentName:function formatComponentName(vm){if(vm.$root===vm){return'root instance';}var name=vm._isVue?vm.$options.name||vm.$options._componentTag:vm.name;return(name?"component <".concat(name,">"):'anonymous component')+(vm._isVue&&vm.$options.__file?" at ".concat(vm.$options.__file):'');}},errorCaptured:function errorCaptured(err,vm,info){var _this32=this;var metadata={};metadata.componentName=this.formatComponentName(vm);metadata.propsData=vm.$options.propsData;if(info!==void 0){metadata.lifecycleHook=info;}configureScope(function(scope){scope.setExtra('config',_this32.config);scope.setTag('version','1.11.1');scope.setTag('apikey',_this32.apiClient.session.apikey);scope.setContext('vue',metadata);});this.sentry.captureException(err);},mounted:function mounted(){this.initSentry();},render:function render(){return this.$slots["default"][0];}};/* eslint-enable */ /* script */var __vue_script__$s=script$s;/* template */ /* style */var __vue_inject_styles__$s=undefined;/* scoped */var __vue_scope_id__$s=undefined;/* module identifier */var __vue_module_identifier__$s=undefined;/* functional template */var __vue_is_functional_template__$s=undefined;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var ErrorBoundary=normalizeComponent({},__vue_inject_styles__$s,__vue_script__$s,__vue_scope_id__$s,__vue_is_functional_template__$s,__vue_module_identifier__$s,false,undefined,undefined,undefined);// Retrieve last element of an array
var last=function last(array){var length=array==null?0:array.length;return length?array[length-1]:undefined;};var CALLBACK_URL_KEY='fs-tab';// Update client representation to include source information
var convertToFileObj=function convertToFileObj(fileReturnedByApi,cloudName){var file=_objectSpread({source:cloudName,sourceKind:'cloud'},fileReturnedByApi);return file;};// Default clouds
var cloudSources=sources.filter(function(s){return s.ui==='cloud';}).map(function(s){return s.name;}).reduce(function(obj,name){obj[name]={status:'initialized'};return obj;},{});var initialState={clouds:_objectSpread({},cloudSources),cloudFolders:{},selectedCloudPath:null};var mutations={SET_CLOUD_PATH:function SET_CLOUD_PATH(state,_ref32){var name=_ref32.name,path=_ref32.path,payload=_ref32.payload;var data=payload;var obj=state.clouds[name];if(obj.contents&&obj.contents[path]){data=obj.contents[path].concat(payload);}Vue.set(obj,'contents',_objectSpread({},obj.contents,_defineProperty({},path,data)));Vue.set(state.clouds[name],'status','ready');},SET_CLOUD_REDIRECT:function SET_CLOUD_REDIRECT(state,_ref33){var name=_ref33.name,auth=_ref33.payload.auth;var redirect=auth.redirect_url;if(!state.clouds[name]){return;}Vue.set(state.clouds[name],'redirect',redirect);Vue.set(state.clouds[name],'status','unauthorized');},SET_CLOUD_NEXT:function SET_CLOUD_NEXT(state,_ref34){var name=_ref34.name,path=_ref34.path,next=_ref34.next;if(!state.clouds[name]){return;}Vue.set(state.clouds[name],'next',_objectSpread({},state.clouds[name].next,_defineProperty({},path,next)));},SET_CLOUD_LOADING:function SET_CLOUD_LOADING(state,name){if(!state.clouds[name]){return;}Vue.set(state.clouds[name],'status','loading');},SET_CLOUD_ERROR:function SET_CLOUD_ERROR(state,name){var cloudObj=state.clouds[name];if(cloudObj){Vue.set(cloudObj,'status','errored');}},REMOVE_CLOUD_PATHS:function REMOVE_CLOUD_PATHS(state,name){if(!state.clouds[name]){return;}state.clouds[name]={status:'initialized'};},REMOVE_ALL_CLOUD_PATHS:function REMOVE_ALL_CLOUD_PATHS(state){state.clouds=_objectSpread({},cloudSources);},SET_CLOUD_FOLDERS:function SET_CLOUD_FOLDERS(state,folders){folders.forEach(function(folder){state.cloudFolders[folder.path]=_objectSpread({},state.cloudFolders[folder.path],{name:folder.name});});},SET_CLOUD_PATH_SELECTED:function SET_CLOUD_PATH_SELECTED(state,path){state.selectedCloudPath=path;},SET_CLOUD_FOLDER_LOADING:function SET_CLOUD_FOLDER_LOADING(state,_ref35){var path=_ref35.path,value=_ref35.value;state.cloudFolders=_objectSpread({},state.cloudFolders,_defineProperty({},path,_objectSpread({},state.cloudFolders[path],{loading:value})));},RESET_CLOUD:function RESET_CLOUD(){// because of some reference we need to clear cloud contents initial state to reset values
Object.keys(initialState.clouds).forEach(function(key){initialState.clouds[key].contents=null;});}};var actions={goToDirectory:function goToDirectory(_ref36,folder){var commit=_ref36.commit,dispatch=_ref36.dispatch,getters=_ref36.getters;var name=getters.currentCloud.name;var path=getters.currentCloud.path;var dirPath=folder.path;dispatch('fetchCloudPath',{name:name,path:dirPath}).then(function(res){if(name===getters.currentCloud.name&&res!==undefined){var newPath=path.concat([dirPath]);var newRoute=['source',name,newPath];commit('CHANGE_ROUTE',newRoute);}});},logout:function logout(_ref37,name){var commit=_ref37.commit,dispatch=_ref37.dispatch,getters=_ref37.getters;// Logout single cloud
if(name){getters.cloudClient.logout(name).then(function(){commit('REMOVE_CLOUD_PATHS',name);commit('REMOVE_SOURCE_FROM_WAITING',name);if(name===getters.currentCloud.name){// Get out of any folders
commit('CHANGE_ROUTE',['source',name]);}commit('REMOVE_SOURCE_FROM_ROUTES',name);dispatch('fetchCloudPath',{name:name,path:'/'});});}else{// Logout all clouds
getters.cloudClient.logout().then(function(){commit('REMOVE_ALL_CLOUD_PATHS');commit('REMOVE_CLOUDS_FROM_WAITING');});}},onFetchSuccess:function onFetchSuccess(context,_ref38){var name=_ref38.name,path=_ref38.path,res=_ref38.res;return new Promise(function(resolve,reject){var cloudObj=res[name];if(cloudObj&&cloudObj.auth&&cloudObj.auth.redirect_url){context.commit('SET_CLOUD_REDIRECT',{name:name,payload:cloudObj});resolve();}else if(cloudObj&&cloudObj.error){var msg=cloudObj.error.text||'Error occurred.';reject(new Error(msg));}else if(cloudObj&&cloudObj.contents){var payload=cloudObj.contents.map(function(file){return convertToFileObj(file,name);});context.commit('SET_CLOUD_NEXT',{name:name,path:path,next:cloudObj.next});// Keep a map of folder names to folder paths (important for FB, Google, etc.)
var folders=payload.filter(function(f){return f.folder;});context.commit('SET_CLOUD_FOLDERS',folders);// Add converted contents to cloud path in state
context.commit('SET_CLOUD_PATH',{name:name,path:path,payload:payload});resolve(payload);}else{reject(new Error('Empty response.'));}});},prefetchClouds:function prefetchClouds(context){// Fetch root folder of each user-defined cloud source
var path='/';var clouds=context.getters.fromSources.filter(function(s){return s.ui==='cloud';}).map(function(s){return s.name;}).reduce(function(obj,name){obj[name]={path:path};if(name==='customsource'){obj[name].customSourceContainer=context.getters.customSourceContainer;obj[name].customSourcePath=context.getters.customSourcePath;}return obj;},{});var keys=Object.keys(clouds);if(keys.length){keys.forEach(function(key){context.commit('SET_CLOUD_LOADING',key);});return context.getters.cloudClient.list(clouds).then(function(res){keys.forEach(function(name){context.dispatch('onFetchSuccess',{name:name,path:path,res:res})// Be silent on rejections
["catch"](function(){context.commit('SET_CLOUD_ERROR',name);});});})["catch"](function(){keys.forEach(function(key){context.commit('SET_CLOUD_ERROR',key);});});}return Promise.resolve();},addCloudFolder:function addCloudFolder(_ref39,_ref40){var state=_ref39.state,commit=_ref39.commit,dispatch=_ref39.dispatch,getters=_ref39.getters;var name=_ref40.name,path=_ref40.path,next=_ref40.next;// path is folder.path
commit('SET_CLOUD_FOLDER_LOADING',{path:path,value:true});return dispatch('fetchCloudPath',{name:name,path:path,load:false,next:next}).then(function(res){if(res){commit('SET_CLOUD_PATH_SELECTED',path);commit('SET_CLOUD_FOLDER_LOADING',{path:path,value:false});// TODO Remove this filter to fetch folders recursively. Maybe an option?
var list=res.filter(function(f){return!f.folder;});list.forEach(function(f){return dispatch('addFile',f);});commit('SET_CLOUD_PATH_SELECTED',null);// Recursively fetch this folder until there are no more next paths
var nextPath=state.clouds[name].next&&state.clouds[name].next[path];if(nextPath&&getters.fileCount!==getters.maxFiles){dispatch('addCloudFolder',{name:name,path:path,next:nextPath});}}})["catch"](function(err){commit('SET_CLOUD_FOLDER_LOADING',{path:path,value:false});return dispatch('showNotification',err.message);});},fetchCloudPath:function fetchCloudPath(_ref41,_ref42){var state=_ref41.state,commit=_ref41.commit,getters=_ref41.getters,dispatch=_ref41.dispatch;var name=_ref42.name,_ref42$path=_ref42.path,path=_ref42$path===void 0?'/':_ref42$path,_ref42$load=_ref42.load,load=_ref42$load===void 0?true:_ref42$load,next=_ref42.next;// Don't fetch path if we already have it cached
var cloudObj=state.clouds[name];if(!next&&cloudObj&&cloudObj.contents&&cloudObj.contents[path]){return Promise.resolve(cloudObj.contents[path]);}// Fetch folder list from CloudRouter API
var payload=_defineProperty({},name,{path:path,next:next});if(name==='customsource'){payload.customsource.customSourceContainer=getters.customSourceContainer;payload.customsource.customSourcePath=getters.customSourcePath;}if(load){commit('SET_CLOUD_LOADING',name);}return getters.cloudClient.list(payload).then(function(res){return dispatch('onFetchSuccess',{name:name,path:path,res:res});})["catch"](function(err){commit('SET_CLOUD_ERROR',name);return dispatch('showNotification',err.message);});},goToLastPath:function goToLastPath(_ref43,name){var getters=_ref43.getters,commit=_ref43.commit;// If we're already on the last path then do nothing
if(getters.currentCloud.name!==name){var lastPath=getters.routesHistory.filter(function(route){return route[1]===name;})// Look for all routes within current cloud
.pop();// Pop most recent route
if(lastPath&&lastPath.length){commit('CHANGE_ROUTE',lastPath);}else{// Default to root folder
commit('CHANGE_ROUTE',['source',name]);}}}};var getters={clouds:function clouds(st){return st.clouds;},cloudFolders:function cloudFolders(st){return st.cloudFolders;},currentCloud:function currentCloud(st,_ref44){var route=_ref44.route;// Current cloud is based on current route, e.g. ['source', 'dropbox']
var name=route[1];var path=route[2]||['/'];var lastPath=last(path);if(st.clouds[name]){return{name:name,path:path,lastPath:lastPath,next:st.clouds[name].next&&st.clouds[name].next[lastPath],redirect:st.clouds[name].redirect,isUnauthorized:st.clouds[name].status==='unauthorized',isErrored:!st.clouds[name].contents&&st.clouds[name].status==='errored',isLoading:st.clouds[name].status==='loading'};}return{};},currentCloudFiles:function currentCloudFiles(st,_ref45){var currentCloud=_ref45.currentCloud;var name=currentCloud.name;var path=currentCloud.lastPath;if(st.clouds[name]&&st.clouds[name].contents){return st.clouds[name].contents[path]||[];}return[];},selectedCloudPath:function selectedCloudPath(st){return st.selectedCloudPath;}};var cloudStore={state:initialState,mutations:mutations,actions:actions,getters:getters};//
var script$t={components:{Blocked:Blocked,DragAndDrop:DragAndDrop,Notifications:Notifications,PickFromSource:PickFromSource,Transform:Transform,UploadSummary:UploadSummary,ErrorBoundary:ErrorBoundary},computed:_objectSpread({},index_esm.mapGetters(['cloudClient','cropFiles','fromSources','globalDropZone','isInlineDisplay','rootId','route','prefetched','uiVisible']),{localPickingAllowed:function localPickingAllowed(){return this.route&&this.route[1]==='local_file_system'||this.globalDropZone;},getClasses:function getClasses(){return{'fsp-picker--inline':this.isInlineDisplay};}}),methods:_objectSpread({},index_esm.mapActions(['addFile','prefetchClouds','showNotification']),{isRootRoute:function isRootRoute(name){return this.route[0]===name;},detectEscPressed:function detectEscPressed(event){if(event.keyCode===27){this.$store.dispatch('cancelPick');this.$root.$destroy();}}}),created:function created(){var _this33=this;if(!this.isInlineDisplay){window.addEventListener('keyup',this.detectEscPressed);}if(!this.prefetched){this.cloudClient.prefetch().then(function(res){// Maybe move this to Vue init?
_this33.$store.commit('SET_PREFETCH',res);_this33.$store.commit('PREFETCH_DONE');_this33.prefetchClouds();// initial folder list needs to be done after prefetch request
})["catch"](function(){return undefined;});}// Add files passed in from crop
var promises=[];if(this.cropFiles&&this.cropFiles.length){[].forEach.call(this.cropFiles,function(file){if(typeof file==='string'){promises.push(_this33.cloudClient.metadata(file));}else{_this33.addFile(file);}});return Promise.all(promises).then(function(res){res.forEach(function(r){if(r.error){throw new Error(r.error);}_this33.addFile(r);});if(_this33.cropFiles.length>1){_this33.$store.commit('CHANGE_ROUTE',['summary']);}})["catch"](function(){_this33.showNotification('Error fetching URL metadata.');setTimeout(function(){_this33.$store.dispatch('cancelPick');_this33.$root.$destroy();},2000);});}if(window.URLSearchParams){var searchParams=new window.URLSearchParams(window.location.search);var sourceName=searchParams.get(CALLBACK_URL_KEY);if(sourceName&&sourceName.length>0){return this.$store.dispatch('goToLastPath',sourceName);}}return this.$store.commit('INITIAL_ROUTE');},destroyed:function destroyed(){window.removeEventListener('keyup',this.detectEscPressed);}};/* script */var __vue_script__$t=script$t;/* template */var __vue_render__$s=function __vue_render__$s(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("transition",{attrs:{appear:"","appear-class":"fsp-picker-appear","appear-to-class":"fsp-picker-appear-to","appear-active-class":"fsp-picker-appear-active"}},[_c("error-boundary",[_c("div",{directives:[{name:"show",rawName:"v-show",value:_vm.uiVisible,expression:"uiVisible"}],"class":_vm.getClasses,attrs:{id:_vm.rootId}},[_c("div",{staticClass:"fsp-picker-holder"},[_vm.isRootRoute("source")?_c("pick-from-source"):_vm._e(),_vm._v(" "),_vm.isRootRoute("summary")?_c("upload-summary"):_vm._e(),_vm._v(" "),_vm.isRootRoute("transform")?_c("transform"):_vm._e(),_vm._v(" "),_c("notifications"),_vm._v(" "),_vm.localPickingAllowed&&!_vm.isRootRoute("transform")?_c("drag-and-drop"):_vm._e()],1)])])],1);};var __vue_staticRenderFns__$s=[];__vue_render__$s._withStripped=true;/* style */var __vue_inject_styles__$t=undefined;/* scoped */var __vue_scope_id__$t=undefined;/* module identifier */var __vue_module_identifier__$t=undefined;/* functional template */var __vue_is_functional_template__$t=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var App=normalizeComponent({render:__vue_render__$s,staticRenderFns:__vue_staticRenderFns__$s},__vue_inject_styles__$t,__vue_script__$t,__vue_scope_id__$t,__vue_is_functional_template__$t,__vue_module_identifier__$t,false,undefined,undefined,undefined);var DISPLAY_MODE_OVERLAY='overlay';var DISPLAY_MODE_INLINE='inline';var DISPLAY_MODE_DROPPANE='dropPane';var isNumber$1=function isNumber$1(thing){return typeof thing==='number';};var isObject$4=function isObject$4(thing){return _typeof2(thing)==='object'&&thing!==null&&Array.isArray(thing)===false;};var isInteger=function isInteger(n){return n%1===0;};var parsers={dropPane:function dropPane(_dropPane){return _dropPane;},displayMode:function displayMode(mode){if([DISPLAY_MODE_OVERLAY,DISPLAY_MODE_INLINE].indexOf(mode)<-1){throw new Error('Wrong display mode');}return mode||DISPLAY_MODE_OVERLAY;},/**
     * @deprecated
     */'dropPane.id':function dropPaneId(id){return id;},'dropPane.overlay':function dropPaneOverlay(overlay){return overlay;},'dropPane.onDragEnter':function dropPaneOnDragEnter(onDragEnter){if(typeof onDragEnter!=='function'){throw new Error('Invalid value for "dropPane.onDragEnter" config option');}return onDragEnter;},'dropPane.onDragLeave':function dropPaneOnDragLeave(onDragLeave){if(typeof onDragLeave!=='function'){throw new Error('Invalid value for "dropPane.onDragLeave" config option');}return onDragLeave;},'dropPane.onDragOver':function dropPaneOnDragOver(onDragOver){if(typeof onDragOver!=='function'){throw new Error('Invalid value for "dropPane.onDragOver" config option');}return onDragOver;},'dropPane.onDrop':function dropPaneOnDrop(onDrop){if(typeof onDrop!=='function'){throw new Error('Invalid value for "dropPane.onDrop" config option');}return onDrop;},'dropPane.onSuccess':function dropPaneOnSuccess(onSuccess){if(typeof onSuccess!=='function'){throw new Error('Invalid value for "dropPane.onSuccess" config option');}return onSuccess;},'dropPane.onError':function dropPaneOnError(onError){if(typeof onError!=='function'){throw new Error('Invalid value for "dropPane.onError" config option');}return onError;},'dropPane.onProgress':function dropPaneOnProgress(onProgress){if(typeof onProgress!=='function'){throw new Error('Invalid value for "dropPane.onProgress" config option');}return onProgress;},'dropPane.onClick':function dropPaneOnClick(onClick){if(typeof onClick!=='function'){throw new Error('Invalid value for "dropPane.onClick" config option');}return onClick;},'dropPane.disableClick':function dropPaneDisableClick(disableClick){if(typeof disableClick!=='boolean'){throw new Error('Invalid value for "dropPane.disableClick" config option');}return disableClick;},'dropPane.showIcon':function dropPaneShowIcon(showIcon){if(typeof showIcon!=='boolean'){throw new Error('Invalid value for "dropPane.showIcon" config option');}return showIcon;},'dropPane.showProgress':function dropPaneShowProgress(showProgress){if(typeof showProgress!=='boolean'){throw new Error('Invalid value for "dropPane.showProgress" config option');}return showProgress;},'dropPane.customText':function dropPaneCustomText(customText){if(typeof customText!=='string'){throw new Error('Invalid value for "dropPane.customText" config option');}return customText;},'dropPane.cropFiles':function dropPaneCropFiles(cropFiles){if(typeof cropFiles!=='boolean'){throw new Error('Invalid value for "dropPane.cropFiles" config option');}return cropFiles;},rootId:function rootId(_rootId){if(typeof _rootId!=='string'){throw new Error('Invalid value for "rootId" config option');}return _rootId;},cleanupImageExif:function cleanupImageExif(cleanup){if(typeof cleanup!=='boolean'&&!(cleanup instanceof Object&&(cleanup.keepOrientation!==undefined||cleanup.keepICCandAPP!==undefined))){throw new Error('Invalid value for "cleanupImageExif" config option');}return cleanup;},fromSources:function fromSources(sourcesDefinedByUser){if(typeof sourcesDefinedByUser==='string'){sourcesDefinedByUser=[sourcesDefinedByUser];}return sourcesDefinedByUser.map(getByName).filter(function(s){return!s.deprecated;});},customSourceContainer:function customSourceContainer(container){if(typeof container!=='string'){throw new Error('Invalid value for "customSourceContainer" config option');}return container;},customSourceName:function customSourceName(name){if(typeof name!=='string'){throw new Error('Invalid value for "customSourceName" config option');}return name;},customSourcePath:function customSourcePath(path){if(typeof path!=='string'){throw new Error('Invalid value for "customSourcePath" config option');}return path;},accept:function accept(acceptDefinedByUser){if(typeof acceptDefinedByUser==='string'){acceptDefinedByUser=[acceptDefinedByUser];}acceptDefinedByUser.forEach(function(oneOfAcceptValues){if(typeof oneOfAcceptValues!=='string'){throw new Error('Invalid value for "accept" config option');}});return acceptDefinedByUser;},concurrency:function concurrency(_concurrency){if(typeof _concurrency!=='number'||!isInteger(_concurrency)||_concurrency<1){throw new Error('Invalid value for "concurrency" config option');}return _concurrency;},maxSize:function maxSize(maxSizeDefinedByUser){if(typeof maxSizeDefinedByUser!=='number'||!isInteger(maxSizeDefinedByUser)||maxSizeDefinedByUser<0){throw new Error('Invalid value for "maxSize" config option');}return maxSizeDefinedByUser;},minFiles:function minFiles(minFilesDefinedByUser){if(typeof minFilesDefinedByUser!=='number'||!isInteger(minFilesDefinedByUser)||minFilesDefinedByUser<0){throw new Error('Invalid value for "minFiles" config option');}return minFilesDefinedByUser;},maxFiles:function maxFiles(maxFilesDefinedByUser){if(typeof maxFilesDefinedByUser!=='number'||!isInteger(maxFilesDefinedByUser)||maxFilesDefinedByUser<0){throw new Error('Invalid value for "maxFiles" config option');}return maxFilesDefinedByUser;},startUploadingWhenMaxFilesReached:function startUploadingWhenMaxFilesReached(startUploadingWhenMaxFilesReachedDefinedByUser){if(typeof startUploadingWhenMaxFilesReachedDefinedByUser!=='boolean'){throw new Error('Invalid value for "startUploadingWhenMaxFilesReached" config option');}return startUploadingWhenMaxFilesReachedDefinedByUser;},loadCss:function loadCss(loadCssDefinedByUser){if(typeof loadCssDefinedByUser==='boolean'&&loadCssDefinedByUser===false||typeof loadCssDefinedByUser==='string'){return loadCssDefinedByUser;}throw new Error('Invalid value for "loadCss" config option');},lang:function lang(langDefinedByUser){if(typeof langDefinedByUser==='boolean'&&langDefinedByUser===false||typeof langDefinedByUser==='string'){return langDefinedByUser;}throw new Error('Invalid value for "lang" config option');},viewType:function viewType(_viewType){if(['list','grid'].indexOf(_viewType)===-1){throw new Error('Invalid view type. Can be "list" or "grid"');}return _viewType;},customText:function customText(_customText){if(isObject$4(_customText)){return _customText;}throw new Error('Invalid value for "customText" config option');},storeTo:function storeTo(storeToDefinedByUser){if(isObject$4(storeToDefinedByUser)){return storeToDefinedByUser;}throw new Error('Invalid value for "storeTo" config option');},uploadConfig:function uploadConfig(_uploadConfig){if(isObject$4(_uploadConfig)){return _uploadConfig;}throw new Error('Invalid value for "uploadConfig" config option');},hideModalWhenUploading:function hideModalWhenUploading(hideWhenUploadingDefinedByUser){if(typeof hideWhenUploadingDefinedByUser!=='boolean'){throw new Error('Invalid value for "hideModalWhenUploading" config option');}return hideWhenUploadingDefinedByUser;},uploadInBackground:function uploadInBackground(uploadInBackgroundDefinedByUser){if(typeof uploadInBackgroundDefinedByUser!=='boolean'){throw new Error('Invalid value for "uploadInBackground" config option');}return uploadInBackgroundDefinedByUser;},allowManualRetry:function allowManualRetry(_allowManualRetry){if(typeof _allowManualRetry!=='boolean'){throw new Error('Invalid value for "allowManualRetry" config option');}return _allowManualRetry;},disableTransformer:function disableTransformer(enableTransformerDefinedByUser){if(typeof enableTransformerDefinedByUser!=='boolean'){throw new Error('Invalid value for "disableTransformer" config option');}return enableTransformerDefinedByUser;},disableThumbnails:function disableThumbnails(_disableThumbnails){if(typeof _disableThumbnails!=='boolean'){throw new Error('Invalid value for "disableThumbnails" config option');}return _disableThumbnails;},disableStorageKey:function disableStorageKey(disableKey){if(typeof disableKey!=='boolean'){throw new Error('Invalid value for "disableStorageKey" config option');}return disableKey;},onUploadStarted:function onUploadStarted(onUploadStartedDefinedByUser){if(typeof onUploadStartedDefinedByUser!=='function'){throw new Error('Invalid value for "onUploadStarted" config option');}return onUploadStartedDefinedByUser;},onFileSelected:function onFileSelected(onFileSelectedDefinedByUser){if(typeof onFileSelectedDefinedByUser!=='function'){throw new Error('Invalid value for "onFileSelected" config option');}return onFileSelectedDefinedByUser;},onFileUploadStarted:function onFileUploadStarted(onFileSelectedDefinedByUser){if(typeof onFileSelectedDefinedByUser!=='function'){throw new Error('Invalid value for "onFileUploadStarted" config option');}return onFileSelectedDefinedByUser;},onFileUploadProgress:function onFileUploadProgress(onFileSelectedDefinedByUser){if(typeof onFileSelectedDefinedByUser!=='function'){throw new Error('Invalid value for "onFileUploadProgress" config option');}return onFileSelectedDefinedByUser;},onFileUploadFinished:function onFileUploadFinished(onFileSelectedDefinedByUser){if(typeof onFileSelectedDefinedByUser!=='function'){throw new Error('Invalid value for "onFileUploadFinished" config option');}return onFileSelectedDefinedByUser;},onFileUploadFailed:function onFileUploadFailed(onFileSelectedDefinedByUser){if(typeof onFileSelectedDefinedByUser!=='function'){throw new Error('Invalid value for "onFileUploadFailed" config option');}return onFileSelectedDefinedByUser;},onFileCropped:function onFileCropped(_onFileCropped){if(typeof _onFileCropped!=='function'){throw new Error('Invalid value for "onFileCropped" config option');}return _onFileCropped;},onOpen:function onOpen(_onOpen){if(typeof _onOpen!=='function'){throw new Error('Invalid value for "onOpen" config option');}return _onOpen;},onCancel:function onCancel(_onCancel){if(typeof _onCancel!=='function'){throw new Error('Invalid value for "onCancel" config option');}return _onCancel;},onClose:function onClose(_onClose){if(typeof _onClose!=='function'){throw new Error('Invalid value for "onClose" config option');}return _onClose;},onUploadDone:function onUploadDone(_onUploadDone){if(typeof _onUploadDone!=='function'){throw new Error('Invalid value for "onUploadDone" config option');}return _onUploadDone;},videoResolution:function videoResolution(res){if(typeof res!=='string'){throw new Error('Invalid value for "videoResolution" config option');}if(['1280x720','640x480','320x240'].indexOf(res)===-1){throw new Error('Invalid value for "videoResolution" config option');}return res;},errorsTimeout:function errorsTimeout(timeout){if(typeof timeout!=='number'||timeout<=0){throw new Error('Timeout must be a number [ms] greater than 0');}return timeout;},/* -----------------
    Transformer options
    ----------------- */transformations:function transformations(transformationsDefinedByUser){if(isObject$4(transformationsDefinedByUser)){return transformationsDefinedByUser;}throw new Error('Invalid value for "transformations" config option');},'transformations.crop':function transformationsCrop(cropDefinedByUser){if(isObject$4(cropDefinedByUser)){return cropDefinedByUser;}if(cropDefinedByUser===true){return{};}if(cropDefinedByUser===false){return false;}throw new Error('Invalid value for "transformations.crop" config option');},'transformations.crop.aspectRatio':function transformationsCropAspectRatio(aspectRatioDefinedByUser){if(isNumber$1(aspectRatioDefinedByUser)){return aspectRatioDefinedByUser;}throw new Error('Invalid value for "transformations.crop.aspectRatio" config option');},'transformations.crop.force':function transformationsCropForce(force){if(typeof force!=='boolean'){throw new Error('Invalid value for "transformations.crop.force" config option');}return force;},'transformations.force':function transformationsForce(force){if(typeof force!=='boolean'){throw new Error('Invalid value for "transformations.force" config option');}return force;},'transformations.circle':function transformationsCircle(circle){if(typeof circle!=='boolean'){throw new Error('Invalid value for "transformations.circle" config option');}return circle;},'transformations.rotate':function transformationsRotate(rotate){if(typeof rotate!=='boolean'){throw new Error('Invalid value for "transformations.rotate" config option');}return rotate;},'transformations.mask':function transformationsMask(mask){if(isObject$4(mask)){if(!mask.url||!mask.type){throw new Error('Mask transformation requires a URL and a type of "png" or "svg".');}return mask;}throw new Error('Invalid value for "transformations.mask" config option');},'transformations.mask.url':function transformationsMaskUrl(maskUrl){if(typeof maskUrl!=='string'){throw new Error('Invalid value for "transformations.mask.url" config option');}return maskUrl;},'transformations.mask.type':function transformationsMaskType(maskType){var allowed=['png','svg'];if(typeof maskType!=='string'||allowed.indexOf(maskType)===-1){throw new Error('Invalid value for "transformations.mask.type" config option');}return maskType;},'transformations.mask.color':function transformationsMaskColor(maskColor){if(typeof maskColor!=='string'){throw new Error('Invalid value for "transformations.mask.color" config option');}return maskColor;},imageMin:function imageMin(minDimensionsDefinedByUser){if(Array.isArray(minDimensionsDefinedByUser)){if(minDimensionsDefinedByUser.length===2){var oneOfElementsIsNotNumber=minDimensionsDefinedByUser.some(function(num){return typeof num!=='number';});if(!oneOfElementsIsNotNumber){return minDimensionsDefinedByUser;}throw new Error('Option "imageMin" requires array of numbers');}throw new Error('Option "imageMin" requires array with exactly two elements: [width, height]');}throw new Error('Invalid value for "imageMin" config option');},imageMax:function imageMax(maxDimensionsDefinedByUser){if(Array.isArray(maxDimensionsDefinedByUser)){if(maxDimensionsDefinedByUser.length===2){var oneOfElementsIsNotNumber=maxDimensionsDefinedByUser.some(function(num){return typeof num!=='number';});if(!oneOfElementsIsNotNumber){return maxDimensionsDefinedByUser;}throw new Error('Option "imageMax" requires array of numbers');}throw new Error('Option "imageMax" requires array with exactly two elements: [width, height]');}throw new Error('Invalid value for "imageMax" config option');},imageDim:function imageDim(maxDimensionsDefinedByUser){if(Array.isArray(maxDimensionsDefinedByUser)){if(maxDimensionsDefinedByUser.length===2){return maxDimensionsDefinedByUser;}throw new Error('Option "imageDim" requires array with exactly two elements: [width, height]');}throw new Error('Invalid value for "imageDim" config option');},imageMinMaxBlock:function imageMinMaxBlock(block){if(typeof block!=='boolean'){throw new Error('imageMinMaxBlock should be boolean');}return block;},container:function container(_container){return _container;},globalDropZone:function globalDropZone(_globalDropZone){if(typeof _globalDropZone!=='boolean'){throw new Error('Invalid value for "globalDropZone" config option');}return _globalDropZone;},exposeOriginalFile:function exposeOriginalFile(expose){if(typeof expose!=='boolean'){throw new Error('Invalid value for "exposeOriginalFile" config option');}return expose;},modalSize:function modalSize(_modalSize){if(Array.isArray(_modalSize)){if(_modalSize.length===2){var oneOfElementsIsNotNumber=_modalSize.some(function(num){return typeof num!=='number';});if(!oneOfElementsIsNotNumber){return _modalSize;}throw new Error('Option "modalSize" requires array of numbers');}throw new Error('Option "modalSize" requires array with exactly two elements: [width, height]');}throw new Error('Invalid value for "modalSize" config option');},customAuthText:function customAuthText(obj){if(!isObject$4(obj)){throw new Error('Invalid value for "customAuthText" config option');}return obj;},useSentryBreadcrumbs:function useSentryBreadcrumbs(_useSentryBreadcrumbs){if(typeof _useSentryBreadcrumbs!=='boolean'){throw new Error('Invalid value for "useSentryBreadcrumbs" config option');}return _useSentryBreadcrumbs;}};var addConfigDefaults=function addConfigDefaults(cfg,env){var config=_objectSpread({},cfg);if(config.fromSources===undefined){config.fromSources=['local_file_system','url','imagesearch','facebook','instagram','googledrive','dropbox'];}if(config.minFiles===undefined){config.minFiles=1;}if(config.maxFiles===undefined){config.maxFiles=1;}if(config.loadCss===undefined){config.loadCss=env.css.main;}if(config.lang===undefined){config.lang='en';}if(config.viewType===undefined){config.viewType='list';}if(config.uploadInBackground===undefined){config.uploadInBackground=true;}if(config.errorsTimeout===undefined){config.errorsTimeout=5000;}if(config.transformations===undefined){config.transformations={crop:{},circle:true,rotate:true};}if(config.transformations&&config.transformations.mask&&config.transformations.mask.color===undefined){config.transformations.mask.color='#000000';}if(config.transformations&&config.transformations.circle===undefined){var oldCircle=config.transformations.crop&&config.transformations.crop.circle;if(oldCircle!==undefined){config.transformations.circle=oldCircle;}}if(config.imageMax===undefined){var oldImageMax=config.transformations.maxDimensions;if(oldImageMax!==undefined){config.imageMax=oldImageMax;}}if(config.imageMin===undefined){var oldImageMin=config.transformations.minDimensions;if(oldImageMin!==undefined){config.imageMin=oldImageMin;}}if(!config.dropPane&&config.displayMode===DISPLAY_MODE_DROPPANE){config.dropPane={};}if(config.dropPane){config.uploadInBackground=false;}if(config.dropPane&&config.dropPane.overlay===undefined){config.dropPane.overlay=true;}if(config.dropPane&&config.dropPane.showIcon===undefined){config.dropPane.showIcon=true;}if(config.dropPane&&config.dropPane.showProgress===undefined){config.dropPane.showProgress=true;}if(config.concurrency===undefined){config.concurrency=4;}if(config.displayMode===undefined){config.displayMode=DISPLAY_MODE_OVERLAY;}if(config.rootId===undefined){config.rootId='__filestack-picker';}if(config.useSentryBreadcrumbs===undefined){config.useSentryBreadcrumbs=true;}return config;};var parseConfig=function parseConfig(config,parentKey){var parsedConfig={};Object.keys(config).forEach(function(configOption){var key=configOption;if(parentKey){key="".concat(parentKey,".").concat(configOption);}var parser=parsers[key];if(typeof config[configOption]==='undefined'){return;}if(parser){var parsed=parser(config[configOption]);if(isObject$4(parsed)&&key.indexOf('transformations')!==-1){parsedConfig[configOption]=parseConfig(parsed,key);}else{parsedConfig[configOption]=parsed;}}else{throw new Error("Unknown config option \"".concat(key,"\""));}});if(parsedConfig.minFiles!==undefined&&parsedConfig.maxFiles!==undefined&&parsedConfig.minFiles>parsedConfig.maxFiles){throw new Error('Config option "minFiles" must be smaller or equal to "maxFiles"');}return parsedConfig;};//
var script$u={components:{Notifications:Notifications},data:function data(){return{cropFilesDone:0,cropFilesOverride:{},isDropping:false};},computed:_objectSpread({},index_esm.mapGetters(['accept','apiClient','canAddMoreFiles','dropPane','fileCount','filesDone','filesList','filesWaiting','maxFiles','maxSize','storeTo','uploadStarted']),{acceptStr:function acceptStr(){if(this.accept){return this.accept.join(',');}return undefined;},containerClasses:function containerClasses(){return{'fsp-drop-pane__container':true,'fsp-drop-pane__container--active':this.isDropping};},filesFinished:function filesFinished(){if(this.cropFilesDone)return this.cropFilesDone;if(this.filesDone.length)return this.filesDone.length;return 0;},iconClasses:function iconClasses(){return{'fsp-drop-pane__icon':true,'fsp-drop-pane__icon--active':this.isDropping};},multiple:function multiple(){return this.maxFiles>1;},progressStyle:function progressStyle(){return{width:"".concat(this.totalProgress,"%")};},totalProgress:function totalProgress(){var cropFiles=lodash_values(this.cropFilesOverride);var list=cropFiles.length?cropFiles:this.filesList;var allPercents=Math.round(list.map(function(f){return f.progress;}).filter(function(n){return n;}).reduce(function(x,y){return x+y;},0)/this.fileCount);if(this.dropPane.onProgress){this.dropPane.onProgress(allPercents);}return allPercents;}}),methods:_objectSpread({},index_esm.mapActions(['addFile','showNotification']),{clearEvent:function clearEvent(event){event.target.value=null;},dragenter:function dragenter(event){event.preventDefault();this.isDropping=true;if(this.dropPane.onDragEnter)this.dropPane.onDragEnter(event);},dragover:function dragover(event){event.preventDefault();if(this.dropPane.onDragOver)this.dropPane.onDragOver(event);},dragleave:function dragleave(){this.isDropping=false;if(this.dropPane.onDragLeave)this.dropPane.onDragLeave();},drop:function drop(event){var _this34=this;event.preventDefault();this.isDropping=false;extractFilesFromDataTransfer(event.dataTransfer).then(function(files){return _this34.callAddFiles(files);});if(this.dropPane.onDrop)this.dropPane.onDrop(event);},incCropFilesDone:function incCropFilesDone(){this.cropFilesDone+=1;},onFilesSelected:function onFilesSelected(event){this.callAddFiles(event.target.files);},paste:function paste(event){var _this35=this;extractFilesFromDataTransfer(event.clipboardData).then(function(files){return _this35.callAddFiles(files);});},openSelectFile:function openSelectFile(){if(this.dropPane.onClick){this.dropPane.onClick();}if(this.dropPane.disableClick){return;}this.$refs.fileUploadInput.click();},callAddFiles:function callAddFiles(files){// validate files before adding them to queue - workaround (maxSize, maxFiles)
if(this.maxFiles&&this.maxFiles>0&&this.maxFiles<files.length){var filesText=this.maxFiles===1?'file':'files';var errorMsg=errors(this.$store.getters.lang,this.$store.getters.customText).ERROR_MAX_FILES_REACHED.replace('{maxFiles}',this.$store.getters.maxFiles).replace('{filesText}',filesText);return this.showNotification(errorMsg);}if(this.maxSize){var errored=[];for(var i=0;i<files.length;i++){if(files[i].size&&files[i].size>this.maxSize){errored.push(files[i].name);}}if(errored.length>0){var _errorMsg=errors(this.$store.getters.lang,this.$store.getters.customText).ERROR_FILES_TOO_BIG.replace('{displayName}',errored.join(', ')).replace('{maxSize}',readableSize(this.maxSize));return this.showNotification(_errorMsg);}}for(var idx=0;idx<files.length;idx++){var file=files[idx];if(!file.name){if(idx>0){file.name="untitled (".concat(idx,")");}else{file.name='untitled';}}this.addFile(file);}return true;}}),mounted:function mounted(){var dropZone=this.dropPane.overlay?this.$refs.dropOverlay:this.$refs.dropZone;var enterZone=this.dropPane.overlay?document:this.$refs.dropZone;enterZone.addEventListener('dragenter',this.dragenter,false);enterZone.addEventListener('paste',this.paste,false);dropZone.addEventListener('dragover',this.dragover,false);dropZone.addEventListener('dragleave',this.dragleave,false);dropZone.addEventListener('drop',this.drop,false);},beforeDestroy:function beforeDestroy(){var dropZone=this.dropPane.overlay?this.$refs.dropOverlay:this.$refs.dropZone;var enterZone=this.dropPane.overlay?document:this.$refs.dropZone;enterZone.removeEventListener('dragenter',this.dragenter);enterZone.removeEventListener('paste',this.paste);dropZone.removeEventListener('dragover',this.dragover);dropZone.removeEventListener('dragleave',this.dragleave);dropZone.removeEventListener('drop',this.drop);},watch:{filesWaiting:{handler:function handler(files){var _this36=this;if(!this.uploadStarted&&files.length&&!this.dropPane.cropFiles){setTimeout(function(){return _this36.$store.dispatch('startUploading');});return;}if(!this.uploadStarted&&files.length&&this.dropPane.cropFiles){// Spawn a new pick instance and use callbacks to override state
var blobs=files.filter(function(f){return f.originalFile;}).map(function(f){return f.originalFile;});var urls=files.filter(function(f){return!f.originalFile&&f.url;}).map(function(f){return f.url;});var clearState=function clearState(){setTimeout(function(){_this36.$store.commit('SET_UPLOAD_STARTED',false);_this36.$store.commit('CLEAR_FILES');},1000);};// Config that uses callbacks to update local component state
var _config={displayMode:DISPLAY_MODE_OVERLAY,onUploadDone:clearState,onCancel:clearState,dropPane:this.dropPane,transformations:this.$store.getters.transformations,onFileUploadProgress:function onFileUploadProgress(file,evt){_this36.$set(_this36.cropFilesOverride,file.uploadId,file);_this36.$set(_this36.cropFilesOverride[file.uploadId],'progress',evt.totalPercent);},onFileUploadFinished:function onFileUploadFinished(){return _this36.incCropFilesDone();},onUploadStarted:function onUploadStarted(){return _this36.$store.commit('SET_UPLOAD_STARTED',true);}};if(this.storeTo&&Object.keys(this.storeTo)){_config.storeTo=this.storeTo;}// New pick instance to handle cropFiles
var picker=new Picker(this.apiClient,_config);picker.crop(blobs.concat(urls));}}}}};/* script */var __vue_script__$u=script$u;/* template */var __vue_render__$t=function __vue_render__$t(){var _vm=this;var _h=_vm.$createElement;var _c=_vm._self._c||_h;return _c("div",{"class":_vm.containerClasses},[_c("notifications"),_vm._v(" "),_c("input",{ref:"fileUploadInput",staticClass:"fsp-drop-pane__input",attrs:{type:"file",accept:_vm.acceptStr,multiple:_vm.multiple,disabled:!_vm.canAddMoreFiles},on:{change:function change($event){return _vm.onFilesSelected($event);},click:function click($event){return _vm.clearEvent($event);}}}),_vm._v(" "),_c("div",{ref:"dropZone",staticClass:"fsp-drop-pane__drop-zone",on:{click:_vm.openSelectFile}}),_vm._v(" "),_vm.dropPane.showIcon?_c("div",{"class":_vm.iconClasses}):_vm._e(),_vm._v(" "),_vm.dropPane.showProgress&&!_vm.uploadStarted?_c("div",{staticClass:"fsp-drop-pane__text"},[_vm._v("\n    "+_vm._s(_vm.dropPane.customText||_vm.t("Drag and Drop, Copy and Paste Files"))+"\n  ")]):_vm.dropPane.showProgress&&_vm.uploadStarted?_c("div",{staticClass:"fsp-drop-pane__text"},[_vm._v("\n    "+_vm._s(_vm.t("Uploaded")+" "+_vm.filesFinished+" "+_vm.t("of")+" "+_vm.fileCount)+"\n    "),_c("div",{staticClass:"fsp-drop-pane__upload-progress",style:_vm.progressStyle})]):_vm._e(),_vm._v(" "),_c("transition",{attrs:{name:"__fs-fade"}},[_c("div",{directives:[{name:"show",rawName:"v-show",value:_vm.dropPane.overlay&&_vm.isDropping,expression:"dropPane.overlay && isDropping"}],ref:"dropOverlay",staticClass:"fsp-drop-pane__overlay"},[_c("div",{staticClass:"fsp-drop-pane__overlay-box"},[_c("div",{staticClass:"fsp-drop-pane__overlay-icon"}),_vm._v(" "),_c("div",{staticClass:"fsp-drop-pane__overlay-header"},[_vm._v("\n          "+_vm._s(_vm.t("Drop your files anywhere"))+"\n        ")])])])])],1);};var __vue_staticRenderFns__$t=[];__vue_render__$t._withStripped=true;/* style */var __vue_inject_styles__$u=undefined;/* scoped */var __vue_scope_id__$u=undefined;/* module identifier */var __vue_module_identifier__$u=undefined;/* functional template */var __vue_is_functional_template__$u=false;/* style inject */ /* style inject SSR */ /* style inject shadow dom */var DropPane=normalizeComponent({render:__vue_render__$t,staticRenderFns:__vue_staticRenderFns__$t},__vue_inject_styles__$u,__vue_script__$u,__vue_scope_id__$u,__vue_is_functional_template__$u,__vue_module_identifier__$u,false,undefined,undefined,undefined);var arrayFindIndex=function arrayFindIndex(arr,predicate,ctx){if(typeof Array.prototype.findIndex==='function'){return arr.findIndex(predicate,ctx);}if(typeof predicate!=='function'){throw new TypeError('predicate must be a function');}var list=Object(arr);var len=list.length;if(len===0){return-1;}for(var i=0;i<len;i++){if(predicate.call(ctx,list[i],i,list)){return i;}}return-1;};// Update client representation to include source information
var convertToFileObj$1=function convertToFileObj$1(fileReturnedByApi){var file=_objectSpread({source:'imagesearch',sourceKind:'cloud'},fileReturnedByApi);return file;};var initialState$1={input:'',isSearching:false,result:null,error:null};var mutations$1={UPDATE_INPUT:function UPDATE_INPUT(state,value){state.input=value;},FETCH_IMAGES_BEGIN:function FETCH_IMAGES_BEGIN(state){state.isSearching=true;},FETCH_IMAGES_SUCCESS:function FETCH_IMAGES_SUCCESS(state,result){state.result=result;state.isSearching=false;},FETCH_IMAGES_ERROR:function FETCH_IMAGES_ERROR(state,error){state.error=error;state.isSearching=false;}};var actions$1={updateSearchInput:function updateSearchInput(context,value){context.commit('UPDATE_INPUT',value);},fetchImages:function fetchImages(context){// Don't proceed if we're already searching
if(context.getters.isSearching){return;}var input=context.getters.imageSearchInput;if(!input){return;}context.commit('FETCH_IMAGES_BEGIN');context.getters.cloudClient.list({imagesearch:{path:"/".concat(input)}}).then(function(res){var cloudObj=res.imagesearch;if(!cloudObj){context.commit('FETCH_IMAGES_ERROR','No response.');context.dispatch('showNotification','An error occurred. Please try again.');}if(cloudObj&&cloudObj.contents){// Add source and sourceKind for imagesearch (cloud API)
cloudObj.contents=cloudObj.contents.map(convertToFileObj$1);// Commit the whole response into state
context.commit('FETCH_IMAGES_SUCCESS',cloudObj);}// No search results for specified input
if(cloudObj&&cloudObj.contents&&cloudObj.contents.length===0){context.dispatch('showNotification',['No search results found for "{search}"',{search:input}]);}if(cloudObj&&cloudObj.error){context.commit('FETCH_IMAGES_ERROR',cloudObj.error);context.dispatch('showNotification','An error occurred. Please try again.');}})["catch"](function(err){context.commit('FETCH_IMAGES_ERROR',err);context.dispatch('showNotification',err.message);});}};var getters$1={isSearching:function isSearching(st){return st.isSearching;},noResultsFound:function noResultsFound(st){return st.result&&st.result.contents.length===0;},resultsFound:function resultsFound(st){return st.result&&st.result.contents.length>0;},imageSearchInput:function imageSearchInput(st){return st.input;},imageSearchResults:function imageSearchResults(st){return st.result&&st.result.contents;}};var imageSearch={state:initialState$1,mutations:mutations$1,actions:actions$1,getters:getters$1};var ATTEMPT_LIMIT=2;var IMG_URLS=['http://cdn.filestackcontent.com/JRgmGyLtQjCFENsiL0SN','http://cdn.filestackcontent.com/F9wSolR8qtkRluW5nGoQ','http://cdn.filestackcontent.com/qLTNxOSpRH2zWhuiro3E'];var initialState$2={connected:{value:true},attempts:0,listeners:{}};var mutations$2={SET_CONNECTION_STATUS:function SET_CONNECTION_STATUS(state,value){state.connected={value:value};},INC_ATTEMPTS:function INC_ATTEMPTS(state){state.attempts+=1;},RESET_ATTEMPTS:function RESET_ATTEMPTS(state){state.attempts=0;},SET_NETWORK_LISTENERS:function SET_NETWORK_LISTENERS(state,listeners){state.listeners=listeners;}};var actions$2={onNetworkError:function onNetworkError(_ref46,override){var attempts=_ref46.state.attempts,commit=_ref46.commit,dispatch=_ref46.dispatch;commit('INC_ATTEMPTS');if(override||attempts>=ATTEMPT_LIMIT){commit('RESET_ATTEMPTS');commit('SET_CONNECTION_STATUS',false);dispatch('pauseAllUploads');}},onNetworkSuccess:function onNetworkSuccess(_ref47){var commit=_ref47.commit,dispatch=_ref47.dispatch;commit('SET_CONNECTION_STATUS',true);commit('RESET_ATTEMPTS');dispatch('retryAllFailedFiles');},checkNetworkNavigator:function checkNetworkNavigator(_ref48){var commit=_ref48.commit,dispatch=_ref48.dispatch,getters=_ref48.getters;if(getters.allowManualRetry){var online=function online(){return dispatch('onNetworkSuccess');};var offline=function offline(){return dispatch('onNetworkError',true);};window.addEventListener('online',online);window.addEventListener('offline',offline);commit('SET_NETWORK_LISTENERS',{online:online,offline:offline});}},removeNetworkListeners:function removeNetworkListeners(_ref49){var getters=_ref49.getters,_ref49$state$listener=_ref49.state.listeners,online=_ref49$state$listener.online,offline=_ref49$state$listener.offline;if(getters.allowManualRetry){window.removeEventListener('online',online);window.removeEventListener('offline',offline);}},checkNetworkXHR:function checkNetworkXHR(_ref50){var dispatch=_ref50.dispatch,getters=_ref50.getters;if(getters.allowManualRetry){var http=new XMLHttpRequest();var baseUrl=IMG_URLS[Math.floor(Math.random()*IMG_URLS.length)];var url="".concat(baseUrl,"?_=").concat(new Date().getTime());http.open('HEAD',url);http.onreadystatechange=function(){if(http.readyState===4){if(http.status){dispatch('onNetworkSuccess');}else{dispatch('onNetworkError');}}};http.send();}}};var getters$2={isConnected:function isConnected(st){return st.connected.value;},isConnectedObj:function isConnectedObj(st){return st.connected;}};var network={state:initialState$2,mutations:mutations$2,actions:actions$2,getters:getters$2};var notificationTime=5000;var mutations$3={ADD_NOTIFICATION:function ADD_NOTIFICATION(state,notification){state.notifications.push(notification);},REMOVE_NOTIFICATION:function REMOVE_NOTIFICATION(state,notificationToBeRemoved){state.notifications=state.notifications.filter(function(notification){return notification!==notificationToBeRemoved;});},REMOVE_ALL_NOTIFICATIONS:function REMOVE_ALL_NOTIFICATIONS(state){state.notifications.splice(0,state.notifications.length);}};var actions$3={showNotification:function showNotification(context,message,options){var notification={};if(Array.isArray(message)&&message.length===2){notification=_objectSpread({message:message[0],params:message[1]},options);}else if(_typeof2(message)==='object'){notification=_objectSpread({},message,{},options);}else{notification=_objectSpread({message:message},options);}// Prevent duplicate notifications
var messages=context.getters.notifications.map(function(n){return n.message;});if(messages.indexOf(message)<0){context.commit('ADD_NOTIFICATION',notification);setTimeout(function(){context.commit('REMOVE_NOTIFICATION',notification);},context.rootGetters.errorsTimeout||notification.timeout||notificationTime);}},removeAllNotifications:function removeAllNotifications(context){context.commit('REMOVE_ALL_NOTIFICATIONS');}};var getters$3={notifications:function notifications(st){return st.notifications;}};var notifications={state:{notifications:[]},mutations:mutations$3,actions:actions$3,getters:getters$3};var mutations$4={FETCH_URL_START:function FETCH_URL_START(state){state.isFetching=true;},FETCH_URL_DONE:function FETCH_URL_DONE(state){state.isFetching=false;}};var actions$4={fetchUrl:function fetchUrl(context,url){context.commit('FETCH_URL_START');return context.getters.cloudClient.metadata(url).then(function(res){if(res.error){context.commit('FETCH_URL_DONE');return context.dispatch('showNotification',res.error);}context.commit('FETCH_URL_DONE');return context.dispatch('addFile',res).then(function(){return true;});})["catch"](function(){context.commit('FETCH_URL_DONE');context.dispatch('showNotification','Error fetching URL metadata.');});}};var getters$4={isUrlFetching:function isUrlFetching(st){return st.isFetching;}};var urlSource={state:{isFetching:false},mutations:mutations$4,actions:actions$4,getters:getters$4};var log$2=logger.context('picker');Vue.use(index_esm);var hasFalseyValues=function hasFalseyValues(obj){var result=true;Object.keys(obj).forEach(function(key){if(obj[key]){result=false;}});return result;};var createStore=function createStore(apiClient,config,onPickerDone,onPickerCancel,initialState){var cloudClient=apiClient.cloud;initialState=_objectSpread({apiClient:apiClient,modules:{'fs-cropper':ENV.vendor.cropper,'fs-fabric':ENV.vendor.fabric,'fs-opentok':ENV.vendor.opentok},cloudClient:cloudClient,config:config,viewType:null,route:[],routesHistory:[],whitelabel:false,blobURLs:{},prefetched:false,mobileNavActive:false,hideSidebar:false,selectLabelIsActive:false},initialState);return new index_esm.Store({state:initialState,modules:{uploadQueue:uploadQueue(apiClient,initialState.uploadQueue),cloudStore:cloudStore,imageSearch:imageSearch,urlSource:urlSource,notifications:notifications,network:network},mutations:{INITIAL_ROUTE:function INITIAL_ROUTE(state){var sources=state.config.fromSources;var source=sources[0];var newRoute=source?['source',source.name]:[];state.route=newRoute;state.routesHistory.push(state.route);if(sources.length===1){state.hideSidebar=true;}else{state.hideSidebar=false;}},SET_PREFETCH:function SET_PREFETCH(state,data){state.whitelabel=data.whitelabel;},CHANGE_ROUTE:function CHANGE_ROUTE(state,newRoute){log$2("Change route from ".concat(JSON.stringify(this.state.route)," to ").concat(JSON.stringify(newRoute)));state.routesHistory.push(state.route);state.route=newRoute;state.mobileNavActive=false;},GO_BACK_WITH_ROUTE:function GO_BACK_WITH_ROUTE(state){var lastRoute=state.routesHistory.pop();if(lastRoute){state.route=lastRoute;}},GO_BACK_WITH_ROUTE_CURRENT_TYPE:function GO_BACK_WITH_ROUTE_CURRENT_TYPE(state){var history=state.routesHistory.reverse();var toTest=this.state.route.join('/');var foundIdx=arrayFindIndex(history,function(el){if(el.join('/')!==toTest){return toTest.indexOf(el.join('/'))===0;}return false;});if(foundIdx>-1){history.splice(0,foundIdx);}history=history.reverse();var lastRoute=history.pop();if(lastRoute){state.route=lastRoute;}},PREFETCH_DONE:function PREFETCH_DONE(state){state.prefetched=true;},REMOVE_SOURCE_FROM_ROUTES:function REMOVE_SOURCE_FROM_ROUTES(state,name){state.routesHistory=state.routesHistory.filter(function(route){return route[1]&&route[1]!==name;});},SET_BLOB_URL:function SET_BLOB_URL(state,_ref51){var uuid=_ref51.uuid,url=_ref51.url;state.blobURLs[uuid]=url;},REMOVE_BLOB_URL:function REMOVE_BLOB_URL(state,uuid){Vue["delete"](state.blobURLs,uuid);},UPDATE_MOBILE_NAV_ACTIVE:function UPDATE_MOBILE_NAV_ACTIVE(state,isActive){state.mobileNavActive=isActive;if(state.config.fromSources.length===1){state.hideSidebar=!isActive;}},UPDATE_SELECT_LABEL_ACTIVE:function UPDATE_SELECT_LABEL_ACTIVE(state,isActive){state.selectLabelIsActive=isActive;},SET_VIEW_TYPE:function SET_VIEW_TYPE(state,type){if(['list','grid'].indexOf(type)===-1){throw new Error("View type ".concat(type," is not supported. Supported types: grid, list"));}state.viewType=type;}},actions:{setViewType:function setViewType(_ref52,type){var commit=_ref52.commit;commit('SET_VIEW_TYPE',type);},allUploadsDone:function allUploadsDone(context){var filesUploaded=convertFileListForOutsideWorld(context.getters.filesDone,context.getters);var filesFailed=convertFileListForOutsideWorld(context.getters.filesFailed,context.getters);var hasDropPane=context.getters.dropPane;var isInlineDisplay=context.getters.isInlineDisplay;if(hasDropPane&&context.getters.dropPane.onSuccess){context.getters.dropPane.onSuccess(filesUploaded);}if(hasDropPane&&context.getters.dropPane.onError){context.getters.dropPane.onError(filesFailed);}context.dispatch('removeNetworkListeners');if(hasDropPane||isInlineDisplay){setTimeout(function(){context.commit('SET_UPLOAD_STARTED',false);context.commit('CLEAR_FILES');},1000);}onPickerDone({filesUploaded:filesUploaded,filesFailed:filesFailed});},cancelPick:function cancelPick(_ref53){var dispatch=_ref53.dispatch,_ref53$getters=_ref53.getters,filesList=_ref53$getters.filesList,exposeOriginalFile=_ref53$getters.exposeOriginalFile;dispatch('cancelAllUploads');dispatch('removeNetworkListeners');if(onPickerCancel){onPickerCancel(convertFileListForOutsideWorld(filesList,{exposeOriginalFile:exposeOriginalFile}));}},updateMobileNavActive:function updateMobileNavActive(context,isActive){context.commit('UPDATE_MOBILE_NAV_ACTIVE',isActive);},updateSelectLabelActive:function updateSelectLabelActive(context,isActive){context.commit('UPDATE_SELECT_LABEL_ACTIVE',isActive);}},getters:{// Users can toggle modal visibility during upload
uiVisible:function uiVisible(st,getters){if(st.config.displayMode===DISPLAY_MODE_OVERLAY&&getters.uploadStarted&&st.config.hideModalWhenUploading){return false;}return true;},// Clients and base config
apiClient:function apiClient(st){return st.apiClient;},cloudClient:function cloudClient(st){return st.cloudClient;},config:function config(st){return st.config;},// Derived state
blobURLs:function blobURLs(st){return st.blobURLs;},isInlineDisplay:function isInlineDisplay(st){return st.config.displayMode===DISPLAY_MODE_INLINE;},isSidebarHidden:function isSidebarHidden(st){return st.hideSidebar;},mobileNavActive:function mobileNavActive(st){return st.mobileNavActive;},prefetched:function prefetched(st){return st.prefetched;},route:function route(st){return st.route;},whitelabel:function whitelabel(st){return st.whitelabel;},routesHistory:function routesHistory(st){return st.routesHistory;},selectLabelIsActive:function selectLabelIsActive(st){return st.selectLabelIsActive;},// Options
accept:function accept(st){return st.config.accept;},viewType:function viewType(st){if(st.viewType){return st.viewType;}var vt=st.config.viewType;st.viewType=vt;return vt;},allowManualRetry:function allowManualRetry(st){return st.config.allowManualRetry;},concurrency:function concurrency(st){return st.config.concurrency;},container:function container(st){return st.config.container;},cropAspectRatio:function cropAspectRatio(st){return st.config.transformations.crop&&st.config.transformations.crop.aspectRatio||NaN;},cropFiles:function cropFiles(st){return st.config.cropFiles;},cropForce:function cropForce(st){return st.config.transformations.crop&&st.config.transformations.crop.force||st.config.transformations&&st.config.transformations.force;},customSourceContainer:function customSourceContainer(st){return st.config.customSourceContainer;},customSourcePath:function customSourcePath(st){return st.config.customSourcePath;},customSourceName:function customSourceName(st){return st.config.customSourceName;},customText:function customText(st){return st.config.customText;},disableStorageKey:function disableStorageKey(st){return st.config.disableStorageKey;},disableThumbnails:function disableThumbnails(st){return st.config.disableThumbnails;},errorsTimeout:function errorsTimeout(st){return st.config.errorsTimeout;},disableTransformer:function disableTransformer(st){return st.config.disableTransformer||hasFalseyValues(st.config.transformations);},dropPane:function dropPane(st){return st.config.dropPane;},exposeOriginalFile:function exposeOriginalFile(st){return st.config.exposeOriginalFile;},fromSources:function fromSources(st){return st.config.fromSources;},globalDropZone:function globalDropZone(st){return st.config.globalDropZone;},imageMin:function imageMin(st){return st.config.imageMin;},imageMax:function imageMax(st){return st.config.imageMax;},imageDim:function imageDim(st){return st.config.imageDim;},imageMinMaxBlock:function imageMinMaxBlock(st){return st.config.imageMinMaxBlock;},lang:function lang(st){return st.config.lang;},maxFiles:function maxFiles(st){return st.config.maxFiles;},maxSize:function maxSize(st){return st.config.maxSize;},minFiles:function minFiles(st){return st.config.minFiles;},modalSize:function modalSize(st){return st.config.modalSize;},onClose:function onClose(st){return st.config.onClose;},onFileSelected:function onFileSelected(st){return st.config.onFileSelected;},onFileUploadStarted:function onFileUploadStarted(st){return st.config.onFileUploadStarted;},onFileCropped:function onFileCropped(st){return st.config.onFileCropped;},onFileUploadProgress:function onFileUploadProgress(st){return st.config.onFileUploadProgress;},onFileUploadFinished:function onFileUploadFinished(st){return st.config.onFileUploadFinished;},onFileUploadFailed:function onFileUploadFailed(st){return st.config.onFileUploadFailed;},onOpen:function onOpen(st){return st.config.onOpen;},onUploadStarted:function onUploadStarted(st){return st.config.onUploadStarted;},rootId:function rootId(st){return st.config.rootId;},startUploadingWhenMaxFilesReached:function startUploadingWhenMaxFilesReached(st){return st.config.startUploadingWhenMaxFilesReached;},storeTo:function storeTo(st){return st.config.storeTo;},transformations:function transformations(st){return st.config.transformations;},uploadConfig:function uploadConfig(st){return st.config.uploadConfig;},uploadInBackground:function uploadInBackground(st){if(!st.config.uploadInBackground){return false;}if(st.config.disableTransformer||st.config.transformations&&!st.config.transformations.crop&&!st.config.transformations.circle&&!st.config.transformations.rotate){return st.config.uploadInBackground;}console.warn('Upload in background can be enabled only when cropper is disabled');return false;},videoResolution:function videoResolution(st){return st.config.videoResolution;},removeExif:function removeExif(st){return st.config.cleanupImageExif;},getModuleUrl:function getModuleUrl(st){return function(moduleName){var path=st.modules[moduleName];var cname=st.apiClient.session.cname;if(cname){path=path.replace('filestackapi.com',cname);}return path;};}}});};var log$3=logger.context('picker');/**
   * @module pick
   */ /**
   * The metadata available on uploaded files returned from pick.
   * @typedef {object} FileMetadata
   * @property {string} filename - Name of the file.
   * @property {string} handle - Filestack handle for the uploaded file.
   * @property {string} mimetype - The MIME type of the file.
   * @property {string} originalPath - The origin of the file, e.g. /Folder/file.jpg.
   * @property {number} size - Size in bytes of the uploaded file.
   * @property {string} source - The source from where the file was picked.
   * @property {string} url - The Filestack CDN URL for the uploaded file.
   * @property {object|undefined} originalFile - Properties of the local binary file.
   * @property {string|undefined} status - Indicates Filestack transit status.
   * @property {string|undefined} key - The hash-prefixed path for files stored in S3.
   * @property {string|undefined} container - The S3 container for the uploaded file.
   * @property {string} uploadId - A uuid for tracking this file in callbacks.
   * @property {object} cropped - An object containing crop position, size, and the original image size
   * @property {object} rotated - An object containing rotation direction and value
   */ /**
   * @callback onFileSelected
   * @param file {object} - File metadata.
   * @example
   *
   * // Using to veto file selection
   * // If you throw any error in this function it will reject the file selection.
   * // The error message will be displayed to the user as an alert.
   * onFileSelected(file) {
   *   if (file.size > 1000 * 1000) {
   *     throw new Error('File too big, select something smaller than 1MB');
   *   }
   * }
   *
   * // Using to change selected file name
   * // (NOTE: currently only works for local and transformed files, no cloud support yet)
   * onFileSelected(file) {
   *   file.name = 'foo';
   *   // It's important to return altered file by the end of this function.
   *   return file;
   * }
   */ /**
   * @callback onUploadStarted
   * @param files {array} - All currently selected files.
   */ /**
   * @callback onFileUploadStarted
   * @param file {object} - File metadata.
   */ /**
   * @callback onFileUploadFinished
   * @param file {object} - File metadata.
   */ /**
   * @callback onFileUploadFailed
   * @param file {object} - File metadata.
   * @param error {error} - Error instance for this upload.
   */ /**
   * @callback onFileUploadProgress
   * @param file {object} - File metadata.
   * @param event {object} - Progress event.
   * @param event.totalPercent {number} - Percent of file uploaded.
   * @param event.totalBytes {number} - Total number of bytes uploaded for this file.
   */ /**
   * Opens the picker UI.
   * @alias module:pick
   * @param [options] {object}
   * @param options.rootId=__filestack-picker {string} - Set id for Vue application mount point
   * @param options.displayMode=overlay {'inline' | 'overlay' | 'dropPane'}- set display mode for picker
   * @param options.container=document.body {string | querySelector | Node} - Picker mount point. Default value is set only in 'overlay' mode
   * @param options.fromSources {string[]} - Valid sources are:
        - `local_file_system` - __Default__
        - `url` - __Default__
        - `imagesearch` - __Default__
        - `facebook` - __Default__
        - `instagram` - __Default__
        - `googledrive` - __Default__
        - `dropbox` - __Default__
        - `video` - Desktop only. Not currently supported in Safari and IE.
        - `audio` - Desktop only. Not currently supported in Safari and IE.
        - `webcam` - Desktop only. Not currently supported in Safari and IE.
        - `evernote`
        - `flickr`
        - `box`
        - `github`
        - `gmail`
        - `picasa`
        - `onedrive`
        - `onedriveforbusiness`
        - `clouddrive`
        - `customsource` - Configure this in your application settings.
   * @param options.accept {string|string[]} - Restrict file types that are allowed to be picked. Formats accepted:
        - `.pdf` <- any file extension
        - '' <- no extension (not supported in local file source)
        - `image/jpeg` <- any mime type commonly known by browsers
        - `image/*` <- accept all types of images
        - `video/*` <- accept all types of video files
        - `audio/*` <- accept all types of audio files
        - `application/*` <- accept all types of application files
        - `text/*` <- accept all types of text files
   * @param options.customSourceContainer {string} - Set the default container for your custom source.
   * @param options.customSourcePath {string} - Set the default path for your custom source container.
   * @param options.concurrency=4 {number} - Max number of files to upload concurrently.
   * @param options.lang=en {string} - Sets locale. Accepts: `ca`, `da`, `de`, `en`, `es`, `fr`, `he`, `it`, `ja`, `ko`, `nl`, `no`, `pl`, `pt`, `sv`, `ru`, `vi`, `zh`.
   * @param options.minFiles=1 {number} - Minimum number of files required to start uploading.
   * @param options.maxFiles=1 {number} - Maximum number of files allowed to upload.
   * @param options.maxSize {number} - Restrict selected files to a maximum number of bytes. (e.g. `10 * 1024 * 1024` for 10MB limit).
   * @param options.startUploadingWhenMaxFilesReached=false {boolean} - Whether to start uploading automatically when maxFiles is hit.
   * @param options.hideWhenUploading=false {boolean} - Hide the picker UI once uploading begins.
   * @param options.uploadInBackground=true {boolean} - Start uploading immediately on file selection.
   * @param options.disableStorageKey=false {boolean} - When true removes the hash prefix on stored files.
   * @param options.disableTransformer=false {boolean} - When true removes ability to edit images.
   * @param options.disableThumbnails=false {boolean} - Disables local image thumbnail previews in the summary screen.
   * @param options.videoResolution=640x480 {string} - Sets the resolution of recorded video. One of "320x240", "640x480" or "1280x720".
   * @param options.transformations {object} - Specify options for images passed to the crop UI.
   * @param options.transformations.crop=true {boolean|object} - Enable crop.
   * @param options.transformations.crop.aspectRatio {number} - Maintain aspect ratio for crop selection. (e.g. 16/9 or 4/3)
   * @param options.transformations.crop.force {boolean} - Force all images to be cropped before uploading.
   * @param options.transformations.circle=true {boolean} - Enable circle crop. __Disabled if `crop.aspectRatio` is defined and not 1. Converts to PNG.__
   * @param options.transformations.rotate=true {boolean} - Enable image rotation.
   * @param options.imageDim {number[]} - Specify image dimensions. e.g. `[800, 600]`. Only for JPEG, PNG, and BMP files.
    Local and cropped images will be resized (upscaled or downscaled) to the specified dimensions before uploading.
    The original height to width ratio is maintained. To resize all images based on the width, set [width, null], e.g. [800, null].
    For the height set [null, height], e.g. [null, 600].
   * @param options.imageMax {number[]} - Specify maximum image dimensions. e.g. `[800, 600]`. Only for JPEG, PNG, and BMP files.
    Images bigger than the specified dimensions will be resized to the maximum size while maintaining the original aspect ratio.
    The output will not be exactly 800x600 unless the imageMax matches the aspect ratio of the original image.
   * @param options.imageMin {number[]} - Specify minimum image dimensions. e.g. `[800, 600]`. Only for JPEG, PNG, and BMP files.
    Images smaller than the specified dimensions will be upscaled to the minimum size while maintaining the original aspect ratio.
    The output will not be exactly 800x600 unless the imageMin matches the aspect ratio of the original image.
   * @param options.uploadConfig {object} - Options for local file uploads.
   * @param options.uploadConfig.partSize=6291456 {number} - Size of each uploaded part (defaults to 6MB). This is overridden when intelligent ingestion is enabled.
   * @param options.uploadConfig.concurrency=3 {number} - Max number of concurrent parts uploading (chunks of files, not whole files).
   * @param options.uploadConfig.intelligent {boolean|string} - Enable/disable intelligent ingestion. If truthy then intelligent ingestion must be enabled in your Filestack application. Passing true/false toggles the global intelligent flow (all parts are chunked and committed). Passing `'fallback'` will only use FII when network conditions may require it (only failing parts will be chunked).
   * @param options.uploadConfig.intelligentChunkSize {number} - Set the default chunk size for intelligent part uploads. Defaults to 8MB on desktop, 1MB on mobile.
   * @param options.uploadConfig.retry=10 {number} - Number of times to retry a failed part of the flow.
   * @param options.uploadConfig.retryFactor=2 {number} - Base factor for exponential backoff.
   * @param options.uploadConfig.timeout=120000 {number} - Time in milliseconds to wait before cancelling requests.
   * @param options.uploadConfig.onRetry {module:filestack~retryCallback} - Called when a retry is initiated.
   * @param options.storeTo {object} - Options for file storage.
   * @param options.storeTo.location {string} - One of `s3`, `gcs`, `rackspace`, `azure`, `dropbox`.
   * @param options.storeTo.region {string} - Valid S3 region for the selected S3 bucket. __S3 only__.
   * @param options.storeTo.container {string}
   * @param options.storeTo.path {string}
   * @param options.storeTo.access {string} - One of `public` or `private`.
   * @param options.onFileSelected {module:pick~onFileSelected} - Called whenever user selects a file.
   * @param options.onFileUploadStarted {module:pick~onFileUploadStarted} - Called when a file begins uploading.
   * @param options.onFileUploadProgress {module:pick~onFileUploadProgress} - Called during multi-part upload progress events. __Local files only__.
   * @param options.onFileUploadFinished {module:pick~onFileUploadFinished} - Called when a file is done uploading.
   * @param options.onFileUploadFailed {module:pick~onFileUploadFailed} - Called when uploading a file fails.
   * @param options.onUploadStarted {module:pick~onUploadStarted} - Called when uploading starts (user initiates uploading).
   * @param options.onOpen {function} - Called when the UI is mounted. As a first argument application component is passed
   * @param options.onCancel {function} - Called when uploads are canceled by user. As a first argument all selected files are passed
   * @param options.onClose {function} - Called after the picker instance is destroyed
   * @param options.onUploadDone {function} - Called when all uploads are finished
   * @param options.allowManualRetry=false {boolean} - Prevent modal close on upload failure and allow users to retry.
   * @param options.globalDropZone {boolean} - Toggle the drop zone to be active on all views. Default is active only on local file source.
   * @param options.exposeOriginalFile {boolean} - When true the originalFile metadata will be the actual File object instead of a POJO.
   * @param options.modalSize {number[]} - Specify [width, height] in pixels of the desktop modal.
   * @param options.dropPane {object} - Configure the picker for drop pane mode.
   * @param options.dropPane.id {string | querySelector} - @deprecated (use {container: 'ID', displayMode: 'dropPane'})  __Required__: Id for the DOM node that will mount the drop pane.
   * @param options.dropPane.overlay=true {boolean} - Toggle the full-page drop zone overlay.
   * @param options.dropPane.onDragEnter {function} - Callback for dragenter events.
   * @param options.dropPane.onDragLeave {function} - Callback for dragleave events.
   * @param options.dropPane.onDragOver {function} - Callback for dragover events.
   * @param options.dropPane.onDrop {function} - Callback for drop events.
   * @param options.dropPane.onSuccess {function} - Callback that is passed a list of uploaded file metadata.
   * @param options.dropPane.onError {function} - Callback that is passed a list of failed file metadata.
   * @param options.dropPane.onProgress {function} - Callback that is passed a number representing total progress percent for all dropped files.
   * @param options.dropPane.onClick {function} - Callback for drop pane click event.
   * @param options.dropPane.disableClick {boolean} - Disable click events on drop pane.
   * @param options.dropPane.showIcon=true {boolean} - Toggle icon element in drop pane.
   * @param options.dropPane.showProgress=true {boolean} - Toggle upload progress display.
   * @param options.dropPane.customText {string} - Customize the text content in the drop pane.
   * @param options.dropPane.cropFiles {boolean} - Toggle the crop UI for dropped files.
   * @param options.customAuthText {object} - Customize text on the cloud authentication screen. Use cloud provider name or 'default' to customize text for the all cloud providers.
   * @param options.useSentryBreadcrumbs=true {boolean} - Use sentry breadcrumbs and send additional information about picker errors.
   *
   * @returns {Picker}
   * @example
   * const config = {
   *   onUploadDone: res => console.log(res),
   *   maxFiles: 20,
   * };
   * const picker = new Picker(apiClient, config);
   * picker.open();
   *
   */var Picker=/*#__PURE__*/function(){function Picker(apiClient,pickerConfig){_classCallCheck(this,Picker);log$3('Starting picker v1.11.1 with config:',pickerConfig);this.app=null;this._mutationObserver=null;this.apiClient=apiClient;this.config=parseConfig(addConfigDefaults(pickerConfig,ENV));this.loadCss=this._loadCssMaybe();/**
       * @Private
       */this._onUploadDone=this.config.onUploadDone;/**
       * @Private
       */this._onOpen=this.config.onOpen;/**
       * @Private
       */this._onCancel=this.config.onCancel;/**
       * @Private
       */this._onClose=this.config.onClose;// initialize vue app and setup container
this._initVue();this._initContainer();this._initMutationObserver();}/**
     * Opens the picker UI
     * @param  {Object} initialStateOverrides
     * @return {Promise<void>} or maybe RxJS subject
     */_createClass(Picker,[{key:"open",value:function open(){var _this37=this;var initialStateOverrides=arguments.length>0&&arguments[0]!==undefined?arguments[0]:{};if(this.app){console.warn('PickerOpen: Picker is already open');return Promise.resolve();}return this.loadCss.then(function(){var onDone=function onDone(res){if(_this37.app){if(_this37.config.displayMode===DISPLAY_MODE_INLINE){_this37.app.$store.commit('GO_BACK_WITH_ROUTE');}if(_this37.config.displayMode===DISPLAY_MODE_OVERLAY){_this37.close();}}if(_this37._onUploadDone){_this37._onUploadDone(res);}};var onCancel=_this37._onCancel;_this37._createPicker(initialStateOverrides,onDone,onCancel);});}/**
     * Opens the picker UI for cropping files
     * @param  {files} Array of Blobs or URLs
     * @return {void}
     */},{key:"crop",value:function crop(files){if([DISPLAY_MODE_OVERLAY,DISPLAY_MODE_INLINE].indexOf(this.config.displayMode)<0){throw new Error('PickerCrop: you can only use crop in overlay and inline display modes');}var fs;if(typeof files==='string'){fs=[files];}else if(files&&files.length){fs=files;}else{throw new Error('PickerCrop: no files found');}var state={config:_objectSpread({},this.config,{cropFiles:fs,hideModalWhenUploading:true,fromSources:[],uploadInBackground:false,maxFiles:fs.length,startUploadingWhenMaxFilesReached:false,disableTransformer:false,transformations:_objectSpread({},this.config.transformations,{crop:_objectSpread({},this.config.transformations.crop,{force:true})})})};if(this.config.transformations&&typeof this.config.transformations.crop==='boolean'&&!this.config.transformations.crop){delete state.config.transformations.crop;}return this.open(state);}/**
     * Destroy picker instance
     * @return {void}
     */},{key:"close",value:function close(){if(!this.app){console.warn('PickerClose: Picker is already closed');return;}this.app.$root.$destroy();this.app=null;}/**
     * Cancels all uploads on picker
     * @return {void}
     */},{key:"cancel",value:function cancel(){if(!this.app){console.warn('PickerCancel: Picker is already closed');return;}this.app.$store.dispatch('cancelPick');}/**
     * Setup Vue application
     * @private
     * @return {void}
     */},{key:"_initVue",value:function _initVue(){// Vue plugins
// Extend configured language object with customText object
var _this$config=this.config,customText=_this$config.customText,lang=_this$config.lang;var langObj=languages[lang];var extendedLanguages=_objectSpread({},languages,_defineProperty({},lang,_objectSpread({},langObj,{},customText)));Vue.use(VueTranslate);Vue.locales(extendedLanguages);Vue.use(VueSessionStorage);}/**
     * Setup picker container
     * @private
     * @return {void}
     */},{key:"_initContainer",value:function _initContainer(){this._component=this.config.displayMode===DISPLAY_MODE_DROPPANE?DropPane:App;this._container=this._getElement(this.config.container,this.config.displayMode===DISPLAY_MODE_OVERLAY)||document.body;}/**
     * If user removes node with app we need to destroy all events connected with it
     * @private
     * @return void
     */},{key:"_initMutationObserver",value:function _initMutationObserver(){var _this38=this;var MutationObserver=window.MutationObserver||window.WebKitMutationObserver||window.MozMutationObserve;if(!MutationObserver){return;}this._mutationObserver=new MutationObserver(function(ev){var mutation=ev[0];if(!mutation){return;}if(_this38.app&&mutation.removedNodes.length>0&&([].indexOf.call(mutation.removedNodes,_this38.app.$el)>-1||[].indexOf.call(mutation.removedNodes,_this38._container)>-1)){_this38.app.$root.$destroy();_this38.app=null;}});}/**
     * Create picker application
     * @private
     * @param  {object} initialStateOverrides
     * @param  {function} onDone
     * @param  {function} onCancel
     * @return {void}
     */},{key:"_createPicker",value:function _createPicker(initialStateOverrides,onDone,onCancel){var _this39=this;var t=this;var config=this.config;var noScroll=config.displayMode===DISPLAY_MODE_OVERLAY;var root=document.createElement('div');if(document.getElementById(config.rootId)){console.warn('Picker Create: Application mount point already exists');return t.app;}this._container.appendChild(root);return new Vue({el:root,store:createStore(this.apiClient,config,onDone,onCancel,initialStateOverrides),render:function render(h){return h(_this39._component);},created:function created(){t.app=this;if(noScroll){document.body.classList.add('fsp-picker--no-scroll');}this.$translate.setLang(config.lang);if(t._mutationObserver){t._mutationObserver.observe(t._container.parentNode,{childList:true});}if(t._onOpen){t._onOpen(t);}},destroyed:function destroyed(){if(t._mutationObserver){t._mutationObserver.disconnect();}document.body.classList.remove('fsp-picker--no-scroll');var el=this.$el;if(el&&el.parentNode){el.parentNode.removeChild(el);}if(t._onClose){t._onClose();}// Clean up vuex
this.$store.commit('RESET_CLOUD');this.$store.commit('CLEAR_FILES');t.app=null;}});}/**
     * returns HtmlNode with given selector or undefined if not found when doNotThrow is enabled
     *
     * @param {string | Node} el
     * @param {boolean} doNotThrow
     * @private
     */},{key:"_getElement",value:function _getElement(el,doNotThrow){var toReturn;if(!el&&!doNotThrow){throw new Error('Filestack Picker Initialize: Container is not defined');}if(typeof el==='string'){if(el.indexOf('.')!==0&&el.indexOf('#')!==0){el="#".concat(el);}toReturn=document.querySelector(el);}else{toReturn=document.body.contains(el)?el:false;}if(!toReturn&&!doNotThrow){throw new Error("Filestack Picker Initialize: Container with - ".concat(el," not found in document"));}return toReturn;}/**
     * Load additional css file and add it to picker
     *
     * @private
     * @return {Promise}
     */},{key:"_loadCssMaybe",value:function _loadCssMaybe(){if(this.config.loadCss===false){return Promise.resolve();}// Apply modal size style
if(this.config.modalSize){var _style=document.createElement('style');_style.innerHTML="\n        @media screen and (min-width: 980px) {\n          .fsp-picker .fsp-modal {\n            width: ".concat(this.config.modalSize[0],"px !important;\n            height: ").concat(this.config.modalSize[1],"px !important;\n          }\n        }\n      ").trim();document.head.appendChild(_style);}var url=this.config.loadCss;var cname=this.apiClient.session.cname;// if there is any cname configured - replace filestack domain with cname and load static from that cname
if(cname&&cname.length){url=url.replace('filestackapi.com',cname);}return loadCss(url);}}]);return Picker;}();var MODULE_ID=knownModuleIds.picker;registerReadyModule(Picker,MODULE_ID);return Picker;}();
//# sourceMappingURL=picker.js.map
