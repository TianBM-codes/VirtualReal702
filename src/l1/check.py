  import h5py, glob, os                    
           
  workspace = r'E:\code\odb-service-design\odb-service-design\tmp'                                                                                                                                                                          
  l1_dir = os.path.join(workspace, 'l1')
                                                                                                                                                                                                                                            
  # 几何                                                                                                                                                                                                                                  
  geom_path = os.path.join(l1_dir, 'geometry', 'PART-1-1.h5')                                                                                                                                                                               
  print("=== 几何 ===")                                                                                                                                                                                                                     
  with h5py.File(geom_path, 'r') as f:                                                                                                                                                                                                      
      print("节点数:", len(f['nodes/labels']))                                                                                                                                                                                              
      total = 0                                                                                                                                                                                                                             
      for etype in f['elements']:                                                                                                                                                                                                           
          cnt = len(f['elements'][etype]['labels'])                                                                                                                                                                                         
          total += cnt                                                                                                                                                                                                                      
          print("  单元类型 {}: {}个".format(etype, cnt))                                                                                                                                                                                   
      print("单元总数:", total)                                                                                                                                                                                                           

  # 结果文件                                                                                                                                                                                                                                
  print("\n=== 结果文件 ===")
  results_dir = os.path.join(l1_dir, 'results')                                                                                                                                                                                             
  for h5f in sorted(glob.glob(os.path.join(results_dir, '*.h5'))):                                                                                                                                                                        
      fname = os.path.basename(h5f)                                                                                                                                                                                                         
      with h5py.File(h5f, 'r') as f:
          frames = len(f['frame_index/frame_values'])                                                                                                                                                                                       
          comps = list(f['meta/components'].asstr())                                                                                                                                                                                      
          print("  {} : {}帧  分量={}".format(fname, frames, comps))