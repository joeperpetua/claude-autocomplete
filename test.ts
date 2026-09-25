let fullName = "Carl Max";

function getNames(fullName: string) {
  return fullName.split(" ");
}

const names = getNames(fullName);

console.log(names);
console.log(names[0]);
console.log(names[1]);
