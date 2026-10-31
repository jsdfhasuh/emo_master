/** One in-flight preparation plus one newest pending item per scope, no starvation. */
export class LatestScopeQueue<T> {
 private slots = new Map<string, { running: boolean; active?: string; shown?: string; pending?: { value:T; signature:string }; epoch:number }>();
 constructor(private process:(value:T)=>Promise<boolean>) {}
 push(scope:string,value:T,signature:string) {
  let slot=this.slots.get(scope);if(!slot){slot={running:false,epoch:0};this.slots.set(scope,slot)}
  if(slot.active===signature||slot.shown===signature)return;
  slot.pending={value,signature};if(!slot.running)void this.drain(slot);
 }
 private async drain(slot:{running:boolean;active?:string;shown?:string;pending?:{value:T;signature:string};epoch:number}) {
  slot.running=true;
  try{while(slot.pending){const item=slot.pending,epoch=slot.epoch;slot.pending=undefined;slot.active=item.signature;
   try{if(await this.process(item.value) && epoch===slot.epoch)slot.shown=item.signature;}finally{slot.active=undefined;}
  }}finally{slot.running=false;}
 }
 invalidate(){for(const slot of this.slots.values()){slot.epoch++;slot.pending=undefined;slot.active=undefined;slot.shown=undefined;}}
}
