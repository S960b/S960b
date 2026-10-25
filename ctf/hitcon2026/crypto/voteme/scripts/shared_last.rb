require '/mnt/data/voteme_work/voteme/ruby/lib/vote_me/curve'
points=File.readlines('/mnt/data/voteme_work/server_points.txt')
scalar=File.readlines('/mnt/data/voteme_work/scalars.txt').last.split[1].to_i
v=File.binread('/mnt/data/voteme_work/last_c.bin')
pos=0
while pos+5<=v.bytesize
 t=v.getbyte(pos);z=v.byteslice(pos+3,2).unpack1('n');body=v.byteslice(pos+5,z)
 if t==22 && body.getbyte(0)==16
  b=body.byteslice(5,65)
  c=VoteMe::Curve.decode(b)
  puts "CLIENT_POINT: #{c.inspect}"
  shared=VoteMe::Curve.multiply(scalar,c)
  puts "SHARED_X: #{shared[0]}"
  puts "SHARED_Y: #{shared[1]}"
 end
 pos+=5+z
end
